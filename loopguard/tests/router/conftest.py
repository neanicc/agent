from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest


NOW = datetime(2026, 7, 16, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def catalog_models():
    from loopguard.router.catalog import ModelSpec

    common = {
        "source": "signed-test-catalog",
        "source_kind": "product_config",
        "observed_at": NOW,
        "effective_at": NOW - timedelta(hours=1),
        "expires_at": NOW + timedelta(days=1),
        "provider_revision": "2026-07-15",
        "capability_evidence": {"tools": "provider-model-card:2026-07-15"},
    }
    return [
        ModelSpec(
            id="fast",
            provider="p",
            surfaces={"managed"},
            efforts={"low", "medium"},
            input_cost_per_million=Decimal("1"),
            output_cost_per_million=Decimal("2"),
            capabilities={"tools"},
            **common,
        ),
        ModelSpec(
            id="deep",
            provider="p",
            surfaces={"managed"},
            efforts={"high"},
            input_cost_per_million=Decimal("10"),
            output_cost_per_million=Decimal("20"),
            capabilities={"tools", "vision"},
            **common,
        ),
        ModelSpec(
            id="unknown-price",
            provider="p",
            surfaces={"managed", "attached"},
            efforts={"low"},
            capabilities={"tools"},
            **common,
        ),
    ]


@pytest.fixture
def repo_snapshot():
    from loopguard.router.features import RepositorySnapshot

    return RepositorySnapshot(
        languages={"python", "typescript"},
        file_count=180,
        changed_file_count=2,
        changed_symbol_count=4,
        dependency_depth=3,
        test_scope="targeted",
        tool_requirements={"code"},
    )


@pytest.fixture
def catalog():
    from loopguard.router.catalog import ModelCatalog, ModelSpec

    common = {
        "provider": "test-provider",
        "surfaces": {"managed"},
        "source": "signed-test-catalog",
        "source_kind": "product_config",
        "observed_at": NOW,
        "effective_at": NOW - timedelta(hours=1),
        "expires_at": NOW + timedelta(days=1),
        "provider_revision": "2026-07-16",
    }
    return ModelCatalog(
        [
            ModelSpec(
                id="fast",
                tags={"fast", "economical"},
                efforts={"low", "medium"},
                input_cost_per_million=Decimal("1"),
                output_cost_per_million=Decimal("2"),
                max_context_tokens=128_000,
                **common,
            ),
            ModelSpec(
                id="standard",
                tags={"standard", "balanced"},
                efforts={"medium", "high"},
                input_cost_per_million=Decimal("3"),
                output_cost_per_million=Decimal("6"),
                max_context_tokens=256_000,
                **common,
            ),
            ModelSpec(
                id="deep",
                tags={"deep", "high-quality"},
                efforts={"high"},
                input_cost_per_million=Decimal("10"),
                output_cost_per_million=Decimal("20"),
                max_context_tokens=512_000,
                **common,
            ),
        ],
        catalog_version="routing-test-v1",
    )


@pytest.fixture
def profile():
    from loopguard.router.features import TaskProfile

    def build(task_type: str, risk: str, **updates):
        payload = {
            "task_type": task_type,
            "risk": risk,
            "complexity_score": {"low": 10, "medium": 45, "high": 85}[risk],
            "requires": {"code"},
            "context_tokens_estimate": 32_000,
            "retry_count": 0,
            "prompt_length": 100,
            "prompt_truncated": False,
            "languages": {"python"},
            "file_count": 100,
            "changed_file_count": 1,
            "changed_symbol_count": 2,
            "dependency_depth": 2,
            "test_scope": "targeted",
            "verification_failures": 0,
            "event_count": 0,
            "loop_fingerprints": set(),
            "sensitivity_markers": set(),
            "task_keywords": {task_type},
        }
        payload.update(updates)
        return TaskProfile.model_validate(payload)

    return build
