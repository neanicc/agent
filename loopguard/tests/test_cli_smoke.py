from typer.testing import CliRunner

from loopguard.cli import app

runner = CliRunner()


def test_inspect_empty(tmp_path):
    p = tmp_path / "empty.jsonl"
    p.write_text("")
    result = runner.invoke(app, ["inspect", str(p)])
    assert result.exit_code == 0
    assert "No events" in result.stdout


def test_demo_offline_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # pause would block on input; force a non-interactive terminate.
    monkeypatch.setattr(
        "loopguard.guard.pause_for_action",
        lambda d: d.model_copy(update={"developer_action": "terminate", "allowed": False}),
    )
    result = runner.invoke(app, ["demo"])
    assert result.exit_code == 0
