from loopguard import LoopGuard, LoopGuardConfig
from loopguard.integrations.litellm_callback import LoopGuardLiteLLMCallback


class _Usage:
    prompt_tokens = 200
    completion_tokens = 50
    total_tokens = 250


class _Resp:
    model = "gpt-4o"
    usage = _Usage()
    choices = [type("C", (), {"message": type("M", (), {"content": "hello"})()})()]


def test_callback_records_real_tokens_and_cost():
    guard = LoopGuard(LoopGuardConfig(action="warn", enable_semantic=False, enable_budget=False))
    cb = LoopGuardLiteLLMCallback(guard)
    cb.log_success_event(
        kwargs={"model": "gpt-4o"}, response_obj=_Resp(), start_time=0, end_time=1
    )
    s = guard.summary()
    assert s["events"] == 1
    assert s["tokens"] == 250
    assert s["cost_usd"] > 0.0  # gpt-4o priced in the table
