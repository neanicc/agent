from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def fixtures() -> Path:
    return Path(__file__).resolve().parents[1] / "fixtures" / "preferences"
