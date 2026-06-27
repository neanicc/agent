from __future__ import annotations

import loopguard.guard as guard_module
from loopguard.demos import run_broken_agent


def fake_pause_inject(decision):
    """Force the inject/correction path without an interactive prompt."""
    decision.developer_action = "inject"
    decision.allowed = True
    decision.suggested_message = "read notes.txt instead"
    return decision


def fake_pause_terminate(decision):
    decision.developer_action = "terminate"
    decision.allowed = False
    return decision


def test_correction_injection_recovers(monkeypatch, tmp_path, capsys):
    # Keep any runs/last.jsonl export under the pytest tmp dir, not the repo.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(guard_module, "pause_for_action", fake_pause_inject)

    run_broken_agent(use_guard=True, action="pause", root=str(tmp_path))

    out = capsys.readouterr().out
    # Recovery reads the real notes.txt file that genuinely exists on disk.
    assert "notes.txt" in out
    assert "This is the file you were actually" in out  # recovery content (truncated to 40 chars)


def test_no_guard_runs_to_exhaustion(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)

    run_broken_agent(use_guard=False, root=str(tmp_path))

    out = capsys.readouterr().out
    assert "still trying" in out
    assert "This is the file you were actually looking for." not in out


def test_offline_demo_uses_real_fs_and_zero_cost(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(guard_module, "pause_for_action", fake_pause_terminate)

    run_broken_agent(use_guard=True, action="pause", root=str(tmp_path))

    out = capsys.readouterr().out
    assert "cost: $0.000" in out  # honest zero cost: no LLM in offline mode
