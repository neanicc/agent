from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from loopguard.control.cloud_relay import CloudRelay, RelayAck, RelayDisconnected
from loopguard.control.events import ControlEvent, EventKind, SessionRef
from loopguard.control.store import EventStore


def _event(index: int) -> ControlEvent:
    return ControlEvent(
        event_id=f"evt_{index}",
        kind=EventKind.TOOL_RESULT,
        source="codex",
        session=SessionRef(host_id="host_1", repo_id="repo_1", session_id="session_1"),
        payload={"index": index},
        created_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )


class FakeCloud:
    def __init__(self, *, disconnect_once_after: int | None = None) -> None:
        self.disconnect_once_after = disconnect_once_after
        self.received: dict[int, dict[str, object]] = {}
        self.calls: list[list[int]] = []

    async def send_events(
        self,
        *,
        repository_handle: str,
        after_local_log_seq: int,
        events: list[dict[str, object]],
    ) -> RelayAck:
        assert repository_handle == "rh_1"
        sequences = [int(item["local_log_seq"]) for item in events]
        self.calls.append(sequences)
        if self.disconnect_once_after is not None:
            through = self.disconnect_once_after
            self.disconnect_once_after = None
            for item in events:
                if int(item["local_log_seq"]) <= through:
                    self.received[int(item["local_log_seq"])] = item
            raise RelayDisconnected(acknowledged_through=through)
        for item in events:
            self.received[int(item["local_log_seq"])] = item
        return RelayAck(
            through_local_log_seq=max(sequences),
            through_cloud_ingest_seq=max(sequences) + 100,
        )


def test_daemon_replays_after_last_ack(tmp_path):
    with EventStore.for_test(tmp_path / "events.db") as store:
        for index in range(1, 7):
            store.append(_event(index))
        cloud = FakeCloud(disconnect_once_after=4)

        asyncio.run(CloudRelay(store, cloud, repository_handle="rh_1").sync())

        assert sorted(cloud.received) == [1, 2, 3, 4, 5, 6]
        assert cloud.calls == [[1, 2, 3, 4, 5, 6], [5, 6]]
        assert store.relay_checkpoint("rh_1") == 6
        assert store.count() == 6


def test_checkpoint_survives_restart_and_ack_cannot_jump_past_batch(tmp_path):
    path = tmp_path / "events.db"
    with EventStore.for_test(path) as store:
        for index in range(1, 4):
            store.append(_event(index))
        asyncio.run(CloudRelay(store, FakeCloud(), repository_handle="rh_1").sync())

    with EventStore.for_test(path) as reopened:
        assert reopened.relay_checkpoint("rh_1") == 3
        cloud = FakeCloud()
        asyncio.run(CloudRelay(reopened, cloud, repository_handle="rh_1").sync())
        assert cloud.calls == []


def test_batches_are_bounded(tmp_path):
    with EventStore.for_test(tmp_path / "events.db") as store:
        for index in range(1, 6):
            store.append(_event(index))
        cloud = FakeCloud()

        asyncio.run(
            CloudRelay(store, cloud, repository_handle="rh_1", batch_size=2).sync()
        )

        assert cloud.calls == [[1, 2], [3, 4], [5]]
