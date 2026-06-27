from loopguard.config import LoopGuardConfig
from loopguard.decision import LoopDecision


def test_new_action_modes_allowed():
    for mode in ["pause", "raise", "warn", "flag", "auto"]:
        assert LoopGuardConfig(action=mode).action == mode


def test_enable_judge_defaults_true():
    assert LoopGuardConfig().enable_judge is True


def test_decision_has_judge_fields():
    d = LoopDecision(judged=True, judge_reasoning="stuck", judge_confidence=0.9)
    assert d.judged is True and d.judge_reasoning == "stuck" and d.judge_confidence == 0.9
