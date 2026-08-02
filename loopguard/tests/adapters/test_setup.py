from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from loopguard.adapters.setup import (
    SetupError,
    SetupRequest,
    apply_safe_fixes,
    build_dx_report,
    purge_data,
    record_dx_metric,
    record_protected_session_metric,
    run_setup,
    uninstall_owned_integrations,
)


class RecordingActions:
    def __init__(self, *, fail_at: str | None = None) -> None:
        self.fail_at = fail_at
        self.calls: list[tuple[str, bool]] = []
        self.writes: list[str] = []

    def execute(self, step: str, *, dry_run: bool) -> str:
        self.calls.append((step, dry_run))
        if step == self.fail_at:
            raise RuntimeError("boom")
        if not dry_run:
            self.writes.append(step)
        return "unchanged" if step == "integration" and len(self.calls) > 5 else "changed"


def test_setup_dry_run_has_no_writes_and_never_approves_trust() -> None:
    actions = RecordingActions()

    result = run_setup(
        SetupRequest(agents=("codex",), scope="user", dry_run=True),
        actions=actions,
    )

    assert actions.writes == []
    assert "trust" not in [step for step, _ in actions.calls]
    assert result.status == "preview"
    assert result.trust_status == "human_review_required"


def test_repeated_setup_is_a_noop_when_actions_are_unchanged() -> None:
    class UnchangedActions(RecordingActions):
        def execute(self, step: str, *, dry_run: bool) -> str:
            self.calls.append((step, dry_run))
            return "unchanged"

    result = run_setup(
        SetupRequest(agents=("claude",), scope="project"),
        actions=UnchangedActions(),
    )

    assert result.changed is False
    assert result.status == "attention_required"


def test_local_metric_failure_never_breaks_setup() -> None:
    def fail_metric(_event: str, _duration: float, _outcome: str) -> None:
        raise OSError("read-only state")

    result = run_setup(
        SetupRequest(agents=("claude",), scope="user"),
        actions=RecordingActions(),
        metric_recorder=fail_metric,
    )

    assert result.status == "attention_required"


def test_partial_failure_produces_exact_resume_command() -> None:
    result = run_setup(
        SetupRequest(agents=("codex", "claude"), scope="user"),
        actions=RecordingActions(fail_at="integration"),
    )

    assert result.status == "failed"
    assert result.outcome_code == "LGD-SETUP-STEP-FAILED"
    assert result.resume_command == (
        "loopguard setup --agent codex,claude --scope user --resume-from integration"
    )


def test_noninteractive_setup_requires_explicit_agents_and_scope() -> None:
    with pytest.raises(SetupError, match="LGD-SETUP-NONINTERACTIVE"):
        SetupRequest(agents=("auto",), scope=None, non_interactive=True)


@pytest.mark.skipif(os.name != "posix", reason="owner-mode repair is a POSIX capability")
def test_safe_fixes_cannot_escape_loopguard_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(mode=0o755)
    owned = home / "owned.json"
    owned.write_text("{}")
    outside = tmp_path / "outside.json"
    outside.write_text("keep")

    fixed = apply_safe_fixes(home, [owned, outside])

    assert fixed == (home, owned)
    assert outside.read_text() == "keep"


def test_uninstall_preserves_unrelated_hooks_and_local_data(tmp_path: Path) -> None:
    config = tmp_path / "hooks.json"
    data = tmp_path / "events.db"
    config.write_text(json.dumps({"hooks": {"Other": [{"command": "friend-hook"}]}}))
    data.write_text("events")

    result = uninstall_owned_integrations(
        agents=("codex",),
        removers={"codex": lambda dry_run: "not_installed"},
        dry_run=False,
    )

    assert result.status == "not_installed"
    assert json.loads(config.read_text())["hooks"]["Other"][0]["command"] == "friend-hook"
    assert data.read_text() == "events"


def test_purge_requires_explicit_confirmation(tmp_path: Path) -> None:
    home = tmp_path / "loopguard"
    home.mkdir()
    (home / "events.db").write_text("events")

    with pytest.raises(SetupError, match="LGD-DATA-CONFIRMATION"):
        purge_data(home, confirmed=False)

    assert home.exists()
    assert purge_data(home, confirmed=True) is True
    assert not home.exists()


def test_dx_metrics_are_local_and_contain_no_freeform_identifiers(tmp_path: Path) -> None:
    record_dx_metric(
        tmp_path,
        event="quickstart",
        duration_seconds=1.25,
        outcome_code="ok",
        timestamp=10,
    )
    record_dx_metric(
        tmp_path,
        event="quickstart",
        duration_seconds=0.75,
        outcome_code="ok",
        timestamp=11,
    )

    report = build_dx_report(tmp_path)
    raw = (tmp_path / "dx-metrics.jsonl").read_text()

    assert report["upload_enabled"] is False
    assert report["time_to_quickstart"] == {
        "samples": 2,
        "median_ms": 1000,
        "last_ms": 750,
        "last_outcome": "ok",
    }
    assert set(json.loads(raw.splitlines()[0])) == {
        "schema_version",
        "event",
        "duration_ms",
        "outcome_code",
        "timestamp",
    }


def test_protected_session_metric_is_once_per_latest_setup_without_session_id(
    tmp_path: Path,
) -> None:
    record_dx_metric(
        tmp_path,
        event="install_detected",
        duration_seconds=0.01,
        outcome_code="supported",
        timestamp=100,
    )

    assert record_protected_session_metric(tmp_path, now=112) is True
    assert record_protected_session_metric(tmp_path, now=120) is False

    rows = [json.loads(line) for line in (tmp_path / "dx-metrics.jsonl").read_text().splitlines()]
    protected = [row for row in rows if row["event"] == "protected_session"]
    assert protected == [
        {
            "schema_version": 1,
            "event": "protected_session",
            "duration_ms": 12000,
            "outcome_code": "ok",
            "timestamp": 112,
        }
    ]


@pytest.mark.skipif(os.name == "nt", reason="symlink creation requires optional Windows policy")
def test_dx_report_refuses_a_symlinked_metrics_file(tmp_path: Path) -> None:
    outside = tmp_path / "outside.jsonl"
    outside.write_text("{}\n")
    home = tmp_path / "home"
    home.mkdir()
    (home / "dx-metrics.jsonl").symlink_to(outside)

    with pytest.raises(SetupError, match="LGD-DX-PATH"):
        build_dx_report(home)
