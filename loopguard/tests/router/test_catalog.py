from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from loopguard.router.catalog import (
    CatalogDocument,
    CatalogTrustError,
    ModelCatalog,
    ModelSpec,
)

NOW = datetime(2026, 7, 16, 14, 0, tzinfo=timezone.utc)


def test_catalog_filters_by_surface_capability_and_budget(catalog_models) -> None:
    catalog = ModelCatalog(catalog_models)

    result = catalog.eligible(
        surface="managed",
        effort="low",
        capabilities={"tools"},
        max_output_cost_per_million=Decimal("5"),
    )

    assert [model.id for model in result] == ["fast"]


def test_unknown_prices_are_never_treated_as_zero(catalog_models) -> None:
    catalog = ModelCatalog(catalog_models)

    budgeted = catalog.eligible(
        surface="attached", effort="low", max_output_cost_per_million=Decimal("5")
    )
    unbudgeted = catalog.eligible(surface="attached", effort="low")

    assert budgeted == []
    assert [model.id for model in unbudgeted] == ["unknown-price"]


def test_eligibility_excludes_models_outside_their_evidence_lifetime(catalog_models) -> None:
    catalog = ModelCatalog(catalog_models)

    assert catalog.eligible(surface="managed", effort="low", at=NOW - timedelta(days=1)) == []
    assert catalog.eligible(surface="managed", effort="low", at=NOW + timedelta(days=2)) == []


def test_catalog_order_is_deterministic_and_duplicate_ids_are_rejected(catalog_models) -> None:
    catalog = ModelCatalog(reversed(catalog_models))

    assert [model.id for model in catalog.models] == ["deep", "fast", "unknown-price"]
    with pytest.raises(ValueError, match="duplicate model id"):
        ModelCatalog([catalog_models[0], catalog_models[0]])


def test_model_specs_are_deeply_immutable_and_strict(catalog_models) -> None:
    model = catalog_models[0]

    with pytest.raises(ValidationError, match="frozen"):
        model.id = "changed"
    assert isinstance(model.surfaces, frozenset)
    with pytest.raises(AttributeError):
        model.surfaces.add("other")
    with pytest.raises(ValidationError):
        ModelSpec(
            **model.model_dump(),
            unexpected=True,
        )


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"output_cost_per_million": Decimal("-0.01")}, "greater than or equal"),
        ({"observed_at": datetime(2026, 7, 16, 14, 0)}, "timezone"),
        ({"expires_at": NOW - timedelta(days=2)}, "expires_at"),
        ({"provider_revision": ""}, "provider_revision"),
        ({"capability_evidence": {"vision": ""}}, "capability_evidence"),
    ],
)
def test_model_metadata_rejects_unsafe_or_ambiguous_values(catalog_models, update, message) -> None:
    payload = catalog_models[0].model_dump()
    payload.update(update)

    with pytest.raises(ValidationError, match=message):
        ModelSpec.model_validate(payload)


def test_signed_document_accepts_valid_content_and_records_provenance(catalog_models) -> None:
    key = b"test-verification-key"
    unsigned = CatalogDocument(
        schema_version=1,
        catalog_version="2026.07.16.1",
        source="loopguard-product-config",
        key_id="product-test-key",
        signature="pending",
        observed_at=NOW,
        effective_at=NOW - timedelta(minutes=5),
        expires_at=NOW + timedelta(days=1),
        models=tuple(catalog_models),
    )
    signed = unsigned.model_copy(
        update={"signature": hmac.new(key, unsigned.signed_payload(), hashlib.sha256).hexdigest()}
    )

    catalog = ModelCatalog.from_signed_document(
        signed,
        verifier=lambda key_id, payload, signature: (
            key_id == "product-test-key"
            and hmac.compare_digest(hmac.new(key, payload, hashlib.sha256).hexdigest(), signature)
        ),
        now=NOW,
    )

    assert catalog.status == "fresh"
    assert catalog.catalog_version == "2026.07.16.1"
    assert catalog.key_id == "product-test-key"
    assert catalog.signature == signed.signature
    assert catalog.stale_reason is None


@pytest.mark.parametrize("failure", ["signature", "expired", "not_effective"])
def test_invalid_signed_update_fails_closed_without_previous_catalog(
    catalog_models, failure
) -> None:
    effective_at = NOW - timedelta(hours=1)
    expires_at = NOW + timedelta(hours=1)
    signature = "valid"
    if failure == "signature":
        signature = "invalid"
    elif failure == "expired":
        expires_at = NOW - timedelta(seconds=1)
    else:
        effective_at = NOW + timedelta(seconds=1)
        expires_at = NOW + timedelta(hours=2)
    document = CatalogDocument(
        catalog_version="candidate",
        source="admin-policy",
        key_id="admin-key",
        signature=signature,
        observed_at=NOW,
        effective_at=effective_at,
        expires_at=expires_at,
        models=tuple(catalog_models),
    )

    with pytest.raises(CatalogTrustError, match=failure):
        ModelCatalog.from_signed_document(
            document,
            verifier=lambda _key_id, _payload, candidate: candidate == "valid",
            now=NOW,
        )


def test_invalid_update_retains_last_known_valid_catalog_as_visibly_stale(
    catalog_models,
) -> None:
    previous = ModelCatalog(catalog_models, catalog_version="last-good")
    invalid = CatalogDocument(
        catalog_version="bad-update",
        source="loopguard-product-config",
        key_id="unknown-key",
        signature="invalid",
        observed_at=NOW,
        effective_at=NOW - timedelta(hours=1),
        expires_at=NOW + timedelta(hours=1),
        models=tuple(catalog_models[:1]),
    )

    retained = ModelCatalog.from_signed_document(
        invalid,
        verifier=lambda *_args: False,
        now=NOW,
        last_known_valid=previous,
    )

    assert retained.status == "stale"
    assert retained.stale_reason == "signature_invalid"
    assert retained.catalog_version == "last-good"
    assert [model.id for model in retained.models] == ["deep", "fast", "unknown-price"]


def test_signed_payload_is_canonical_and_excludes_signature(catalog_models) -> None:
    document = CatalogDocument(
        catalog_version="v1",
        source="provider-discovery",
        key_id="provider-key",
        signature="secret-signature",
        observed_at=NOW,
        effective_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        models=tuple(reversed(catalog_models)),
    )

    payload = document.signed_payload()

    assert b"secret-signature" not in payload
    assert payload == document.signed_payload()
    assert payload.index(b'"id":"deep"') < payload.index(b'"id":"fast"')
    assert b'"capabilities":["tools","vision"]' in payload
