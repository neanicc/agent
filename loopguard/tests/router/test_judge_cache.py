from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from loopguard import LoopEvent, LoopGuard, LoopGuardConfig
from loopguard.judge import JudgeVerdict, LLMJudge
from loopguard.providers.base import LLMResult
from loopguard.router.budgets import SessionBudget
from loopguard.router.judge_cache import (
    CachePersistenceError,
    JudgeIncidentCache,
    incident_cache_key,
)


class _Judge:
    model = "judge/test-v1"

    def __init__(self, verdict: JudgeVerdict | None = None) -> None:
        self.verdict = verdict or JudgeVerdict(is_loop=True, reasoning="stuck")
        self.calls = 0
        self.lock = threading.Lock()

    def judge(self, events, task=None, detector=None):
        with self.lock:
            self.calls += 1
        return self.verdict.model_copy(deep=True)


def test_identical_incidents_across_runs_reuse_one_validated_result() -> None:
    judge = _Judge()
    budget = _budget()
    guard = _guard(judge, budget)

    _trip(guard, "run-a", "same.py")
    _trip(guard, "run-b", "same.py")

    assert judge.calls == 1
    assert budget.spent("judge") == Decimal("0.01")


def test_different_arguments_and_context_do_not_collide() -> None:
    one = incident_cache_key(
        policy_version="p1",
        detector_kind="exact",
        events=_events("run-a", "one.py"),
        judge_model="judge/test-v1",
        prompt_version="prompt-v1",
        task="repair parser",
        context="python repo",
    )
    changed_argument = incident_cache_key(
        policy_version="p1",
        detector_kind="exact",
        events=_events("run-b", "two.py"),
        judge_model="judge/test-v1",
        prompt_version="prompt-v1",
        task="repair parser",
        context="python repo",
    )
    changed_context = incident_cache_key(
        policy_version="p1",
        detector_kind="exact",
        events=_events("run-c", "one.py"),
        judge_model="judge/test-v1",
        prompt_version="prompt-v1",
        task="repair parser",
        context="typescript repo",
    )

    assert one.incident_fingerprint != changed_argument.incident_fingerprint
    assert one.incident_fingerprint != changed_context.incident_fingerprint


def test_run_id_and_raw_secrets_are_not_part_of_cache_identity() -> None:
    first = incident_cache_key(
        policy_version="p1",
        detector_kind="exact",
        events=_events("run-secret-a", "same.py", token="sk-secretvalue123"),
        judge_model="judge/test-v1",
        prompt_version="prompt-v1",
        task="token=sk-secretvalue123 repair",
    )
    second = incident_cache_key(
        policy_version="p1",
        detector_kind="exact",
        events=_events("run-secret-b", "same.py", token="sk-othersecret456"),
        judge_model="judge/test-v1",
        prompt_version="prompt-v1",
        task="token=sk-othersecret456 repair",
    )

    assert first == second
    assert "secret" not in repr(first).lower()


def test_policy_prompt_and_model_versions_invalidate_cache_identity() -> None:
    base = _key()

    assert base != _key(policy_version="p2")
    assert base != _key(prompt_version="prompt-v2")
    assert base != _key(judge_model="judge/test-v2")


def test_deferred_failures_are_not_cached_and_reserve_each_real_attempt() -> None:
    judge = _Judge(JudgeVerdict(is_loop=True, reasoning="timeout", confidence=0, validated=False))
    budget = _budget()
    guard = _guard(judge, budget)

    _trip(guard, "run-a", "same.py")
    _trip(guard, "run-b", "same.py")

    assert judge.calls == 2
    assert budget.spent("judge") == Decimal("0.02")


def test_denied_reservation_returns_detector_decision_without_calling_judge() -> None:
    judge = _Judge()
    budget = SessionBudget(
        total=Decimal("1"),
        guard_overhead=Decimal("0"),
        judge=Decimal("0"),
    )
    guard = _guard(judge, budget)

    decision = _trip(guard, "run-a", "same.py")

    assert judge.calls == 0
    assert decision.tripped is True
    assert decision.judged is False
    assert decision.judge_reasoning == "judge skipped: service_budget_exhausted"


def test_raising_judge_fails_safe_and_is_retried_not_cached() -> None:
    class RaisingJudge(_Judge):
        def judge(self, events, task=None, detector=None):
            with self.lock:
                self.calls += 1
            raise TimeoutError("provider timeout")

    judge = RaisingJudge()
    guard = _guard(judge, _budget())

    first = _trip(guard, "run-a", "same.py")
    second = _trip(guard, "run-b", "same.py")

    assert judge.calls == 2
    assert first.tripped and second.tripped
    assert first.judge_reasoning == "judge unavailable; detector decision retained"


def test_concurrent_identical_incidents_single_flight_compute_once() -> None:
    cache = JudgeIncidentCache(ttl_seconds=60, max_entries=10)
    calls = 0
    lock = threading.Lock()

    def compute():
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.05)
        return JudgeVerdict(is_loop=True, reasoning="single flight")

    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(
            executor.map(lambda _index: cache.get_or_compute(_key(), compute), range(12))
        )

    assert calls == 1
    assert sum(result.computed for result in results) == 1
    assert all(result.verdict.reasoning == "single flight" for result in results)


def test_ttl_and_lru_bounds_are_enforced() -> None:
    now = [100.0]
    cache = JudgeIncidentCache(ttl_seconds=5, max_entries=2, clock=lambda: now[0])
    calls = 0

    def compute():
        nonlocal calls
        calls += 1
        return JudgeVerdict(is_loop=True, reasoning=str(calls))

    cache.get_or_compute(_key(detector_kind="one"), compute)
    cache.get_or_compute(_key(detector_kind="two"), compute)
    cache.get_or_compute(_key(detector_kind="three"), compute)
    cache.get_or_compute(_key(detector_kind="one"), compute)
    assert calls == 4
    now[0] += 6
    cache.get_or_compute(_key(detector_kind="three"), compute)
    assert calls == 5

    cache.clear()
    cache.get_or_compute(_key(detector_kind="three"), compute)
    assert calls == 6


def test_persisted_cache_is_encrypted_owner_only_and_reusable(tmp_path) -> None:
    path = tmp_path / "private" / "judge.cache"
    key = b"k" * 32
    cache = JudgeIncidentCache(
        ttl_seconds=60,
        max_entries=10,
        path=path,
        encryption_key=key,
    )
    cache.get_or_compute(
        _key(), lambda: JudgeVerdict(is_loop=True, reasoning="sensitive cached reasoning")
    )

    payload = path.read_bytes()
    assert b"sensitive cached reasoning" not in payload
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600
    restored = JudgeIncidentCache(
        ttl_seconds=60,
        max_entries=10,
        path=path,
        encryption_key=key,
    )
    result = restored.get_or_compute(
        _key(), lambda: JudgeVerdict(is_loop=False, reasoning="should not run")
    )
    assert result.computed is False
    assert result.verdict.reasoning == "sensitive cached reasoning"

    with pytest.raises(CachePersistenceError, match="validation"):
        JudgeIncidentCache(
            ttl_seconds=60,
            max_entries=10,
            path=path,
            encryption_key=b"z" * 32,
        )


def test_malformed_typed_judge_output_is_deferred_and_not_cacheable() -> None:
    class Provider:
        model = "judge/test-v1"

        def complete(self, *_args, **_kwargs):
            return LLMResult(text='{"is_loop":"false","reasoning":"coerced"}')

    verdict = LLMJudge(Provider()).judge(_events("run", "same.py"))

    assert verdict.is_loop is True
    assert verdict.validated is False
    assert verdict.confidence == 0


def _guard(judge: _Judge, budget: SessionBudget) -> LoopGuard:
    return LoopGuard(
        LoopGuardConfig(
            action="flag",
            enable_semantic=False,
            enable_pingpong=False,
            enable_budget=False,
            exact_threshold=2,
            judge_reservation_usd=Decimal("0.01"),
            judge_policy_version="policy-v1",
            judge_prompt_version="prompt-v1",
        ),
        judge=judge,
        budget_callback=budget.reserve,
    )


def _trip(guard: LoopGuard, run_id: str, path: str):
    guard.observe(_event(run_id, path), task="repair parser")
    return guard.observe(_event(run_id, path), task="repair parser")


def _events(run_id: str, path: str, **arguments):
    return [_event(run_id, path, **arguments), _event(run_id, path, **arguments)]


def _event(run_id: str, path: str, **arguments):
    return LoopEvent(
        run_id=run_id,
        agent="agent",
        kind="tool_call",
        tool_name="read_file",
        tool_args={"path": path, **arguments},
    )


def _budget() -> SessionBudget:
    return SessionBudget(
        total=Decimal("1"),
        guard_overhead=Decimal("0.05"),
        judge=Decimal("0.05"),
    )


def _key(**updates):
    payload = {
        "policy_version": "p1",
        "detector_kind": "exact",
        "events": _events("run", "same.py"),
        "judge_model": "judge/test-v1",
        "prompt_version": "prompt-v1",
        "task": "repair parser",
    }
    payload.update(updates)
    return incident_cache_key(**payload)
