from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from loopguard.context.capability import CapabilityIssuer


class ManualClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 7, 15, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.current


def _private_home(tmp_path: Path) -> Path:
    home = tmp_path / "state"
    home.mkdir(mode=0o700)
    return home


def test_capability_is_bound_authenticated_and_single_use(tmp_path: Path) -> None:
    home = _private_home(tmp_path)
    clock = ManualClock()
    with CapabilityIssuer(home, clock=clock) as issuer:
        issued = issuer.issue(
            host_id="host",
            repo_id="repo",
            session_id="session",
            allowed_tools=["get_repo_state"],
        )
        token_file = issuer.write_token(issued)
        capability = issuer.consume_file(token_file)

        assert capability.repo_id == "repo"
        assert capability.session_id == "session"
        assert capability.allowed_tools == ["get_repo_state"]
        assert not token_file.exists()
        with pytest.raises(ValueError, match="already consumed"):
            issuer.consume_token(issued.token)


def test_capability_rejects_tampering_and_expiry(tmp_path: Path) -> None:
    home = _private_home(tmp_path)
    clock = ManualClock()
    with CapabilityIssuer(home, clock=clock) as issuer:
        issued = issuer.issue(host_id="host", repo_id="repo", session_id="session")
        envelope = json.loads(issued.token)
        envelope["capability"]["repo_id"] = "other"
        tampered = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
        with pytest.raises(ValueError, match="authentication"):
            issuer.consume_token(tampered)

        short = issuer.issue(
            host_id="host",
            repo_id="repo",
            session_id="session",
            ttl=timedelta(seconds=1),
        )
        clock.current += timedelta(seconds=2)
        with pytest.raises(ValueError, match="expired"):
            issuer.consume_token(short.token)
