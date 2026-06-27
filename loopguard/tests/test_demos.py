from __future__ import annotations

import loopguard.guard as guard_module
from loopguard.demos import run_broken_agent


def fake_pause(decision):
    """Force the inject/correction path without an interactive prompt."""
    decision.developer_action = "inject"
    decision.allowed = True
    decision.suggested_message = "read pyproject.toml instead"
    return decision


def test_correction_injection_recovers(monkeypatch, tmp_path, capsys):
    # Keep any runs/last.jsonl export under the pytest tmp dir, not the repo.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(guard_module, "pause_for_action", fake_pause)

    run_broken_agent(use_guard=True, action="pause")

    out = capsys.readouterr().out
    assert "Success: project metadata found." in out
    assert "pyproject.toml" in out


def test_no_guard_runs_to_exhaustion(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)

    run_broken_agent(use_guard=False)

    out = capsys.readouterr().out
    assert "still trying" in out
    assert "Success: project metadata found." not in out
