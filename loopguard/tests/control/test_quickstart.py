from __future__ import annotations

import json
import os
import stat
import time

import pytest
from typer.testing import CliRunner

from loopguard.cli import app


runner = CliRunner()


@pytest.mark.skipif(os.name != "posix", reason="named-pipe support is capability-gated")
def test_quickstart_reaches_real_guard_without_key_or_permanent_install(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    started = time.monotonic()

    result = runner.invoke(app, ["quickstart", "--home", str(tmp_path)])

    assert result.exit_code == 0
    assert time.monotonic() - started < 10
    assert "Loop detected before another paid turn" in result.stdout
    assert "No model or API key used" in result.stdout
    assert "Next: loopguard setup --agent auto" in result.stdout
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="named-pipe support is capability-gated")
def test_quickstart_json_has_stable_events_timings_and_policy_decision(tmp_path):
    result = runner.invoke(
        app,
        ["quickstart", "--home", str(tmp_path), "--json"],
    )

    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["schema_version"] == 1
    assert body["model_used"] is False
    assert body["api_key_used"] is False
    assert body["telemetry_sent"] is False
    assert body["decision"]["action"] == "request_approval"
    assert body["decision"]["metadata"]["detector"] == "exact"
    assert [event["name"] for event in body["events"]] == [
        "quickstart.started",
        "daemon.ready",
        "event.acknowledged",
        "loop.detected",
        "quickstart.cleaned",
    ]
    assert all(event["elapsed_ms"] >= 0 for event in body["events"])
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_quickstart_rejects_unsafe_existing_home_without_changing_permissions(tmp_path):
    os.chmod(tmp_path, 0o755)

    result = runner.invoke(
        app,
        ["quickstart", "--home", str(tmp_path), "--json"],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["code"] == "LGD-STATE-002"
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o755
    assert list(tmp_path.iterdir()) == []
