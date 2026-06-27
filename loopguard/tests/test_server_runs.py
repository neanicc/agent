import threading
import time

from loopguard.decision import LoopDecision
from loopguard.judge import JudgeVerdict
from loopguard.providers.base import LLMResult
from loopguard.server.runs import Run, RunRegistry, apply_intervention
from loopguard.server.schemas import InterveneRequest, StartRunRequest, decision_message


def test_start_run_defaults():
    r = StartRunRequest()
    assert r.mode == "flag" and r.model == "cerebras/gpt-oss-120b" and r.provider == "auto"


def test_intervene_request_validates_action():
    assert InterveneRequest(action="approve").message is None
    assert InterveneRequest(action="inject", message="do x").message == "do x"


def test_decision_message_shape():
    d = LoopDecision(
        tripped=True, detector="semantic", similarity=0.94, reason="loop",
        judged=True, judge_reasoning="stuck", judge_confidence=0.9,
        suggested_message="read pyproject.toml",
    )
    msg = decision_message(d)
    assert msg["type"] == "decision_required"
    assert msg["data"]["detector"] == "semantic"
    assert msg["data"]["judge_reasoning"] == "stuck"
    assert msg["data"]["suggested_message"] == "read pyproject.toml"


class _TC:
    def __init__(self, i):
        self.id = f"c{i}"
        self.type = "function"
        self.function = type("F", (), {"name": "read_file",
                                       "arguments": '{"path": "package.json"}'})()


class _LoopProvider:
    model = "fake/model"

    def __init__(self):
        self.n = 0

    def complete(self, messages, *, tools=None, temperature=0.2, max_tokens=512,
                 response_format=None):
        for m in messages[2:]:
            if m.get("role") == "user" and "STOP" in str(m.get("content", "")):
                return LLMResult(text="done", prompt_tokens=1, completion_tokens=1, total_tokens=2)
        self.n += 1
        return LLMResult(text="", tool_calls=[_TC(self.n)], prompt_tokens=10,
                         completion_tokens=2, total_tokens=12, cost_usd=0.001)


class _Judge:
    def judge(self, events, task=None, detector=None):
        return JudgeVerdict(is_loop=True, reasoning="stuck", suggested_correction="STOP now",
                            cost_usd=0.0005)


def test_apply_intervention_maps_actions():
    d = LoopDecision(tripped=True, suggested_message="judge fix")
    apply_intervention(d, "approve", None)
    assert d.allowed and d.developer_action == "inject" and d.suggested_message == "judge fix"

    d2 = LoopDecision(tripped=True)
    apply_intervention(d2, "inject", "custom")
    assert d2.allowed and d2.suggested_message == "custom"

    d3 = LoopDecision(tripped=True)
    apply_intervention(d3, "terminate", None)
    assert d3.allowed is False and d3.developer_action == "terminate"

    d4 = LoopDecision(tripped=True, suggested_message="x")
    apply_intervention(d4, "continue_once", None)
    assert d4.allowed and d4.suggested_message is None


def test_flag_run_pauses_then_resumes_on_approve(tmp_path):
    messages = []
    run = Run(id="r1", mode="flag", model="fake/model", emit=messages.append, root=str(tmp_path))
    t = threading.Thread(target=run.execute, args=(_LoopProvider(), _Judge()), daemon=True)
    t.start()

    for _ in range(100):
        if run.state.status == "awaiting_decision":
            break
        time.sleep(0.02)
    assert run.state.status == "awaiting_decision"
    assert any(m["type"] == "decision_required" for m in messages)

    run.intervene("approve")
    t.join(timeout=5)
    assert run.state.status in ("completed", "stopped")
    assert any(m["type"] == "done" for m in messages)


def test_terminate_stops_the_run(tmp_path):
    run = Run(id="r2", mode="flag", model="fake/model", emit=lambda m: None, root=str(tmp_path))
    t = threading.Thread(target=run.execute, args=(_LoopProvider(), _Judge()), daemon=True)
    t.start()
    for _ in range(100):
        if run.state.status == "awaiting_decision":
            break
        time.sleep(0.02)
    run.intervene("terminate")
    t.join(timeout=5)
    assert run.state.stopped_by_guard is True
    assert run.state.status == "stopped"


def test_registry_create_get_list():
    reg = RunRegistry()
    run = reg.create("abc", "auto", "m", emit=lambda m: None)
    assert reg.get("abc") is run
    assert reg.get("missing") is None
    assert any(s["id"] == "abc" for s in reg.list())
