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
