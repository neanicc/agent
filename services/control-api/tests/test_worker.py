from __future__ import annotations

import sys
from types import ModuleType

import pytest
from temporalio import activity

from loopguard_api.repair_temporal import ACTIVITIES
from loopguard_api.worker import configured_activities, load_activity_factory


def _module_with_activities() -> ModuleType:
    module = ModuleType("test_worker_adapter")
    functions = []
    for name in (*ACTIVITIES, "publish", "release_resources"):
        async def run(payload: dict[str, str]) -> dict[str, str]:
            return {"artifact_id": payload["repair_id"]}
        run.__name__ = name
        functions.append(activity.defn(name=name)(run))
    module.create_activities = lambda: functions
    return module


def test_worker_requires_the_exact_durable_activity_contract(monkeypatch) -> None:
    module = _module_with_activities()
    monkeypatch.setitem(sys.modules, module.__name__, module)
    result = configured_activities(f"{module.__name__}:create_activities")
    assert len(result) == len(ACTIVITIES) + 2


def test_worker_rejects_missing_activity(monkeypatch) -> None:
    module = _module_with_activities()
    complete = module.create_activities()
    module.create_activities = lambda: complete[:-1]
    monkeypatch.setitem(sys.modules, module.__name__, module)
    with pytest.raises(ValueError):
        configured_activities(f"{module.__name__}:create_activities")


@pytest.mark.parametrize(
    "reference",
    ["missing", "_private:factory", "module:_factory", "module:", ":factory"],
)
def test_worker_rejects_unsafe_factory_references(reference: str) -> None:
    with pytest.raises(ValueError):
        load_activity_factory(reference)
