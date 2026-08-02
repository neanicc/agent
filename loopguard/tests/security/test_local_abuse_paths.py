from __future__ import annotations

from pathlib import Path

import pytest

from loopguard.security.boundaries import validate_hook_artifact


@pytest.mark.parametrize(
    "supplied",
    (
        "../../.ssh/id_ed25519",
        "/etc/passwd",
        r"C:\Users\developer\.ssh\id_ed25519",
        "artifacts/../secret",
        "",
    ),
)
def test_hook_payload_cannot_escape_local_state_directory(
    tmp_path: Path,
    supplied: str,
) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)

    result = validate_hook_artifact(
        {"artifact_path": supplied},
        state_directory=state,
    )

    assert result.code == "invalid_path"
    assert result.path is None


def test_hook_payload_rejects_symlink_escape(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    secret = tmp_path / "secret"
    secret.write_text("not hook data")
    (state / "artifact").symlink_to(secret)

    result = validate_hook_artifact(
        {"artifact_path": "artifact"},
        state_directory=state,
    )

    assert result.code == "invalid_path"


def test_hook_payload_accepts_existing_regular_artifact(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    artifact = state / "artifacts" / "event.json"
    artifact.parent.mkdir()
    artifact.write_text("{}")

    result = validate_hook_artifact(
        {"artifact_path": "artifacts/event.json"},
        state_directory=state,
    )

    assert result.code == "accepted"
    assert result.path == artifact
