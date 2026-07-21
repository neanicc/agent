from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from loopguard_api.app import create_app
from loopguard_api.authorization import Permission, Principal
from loopguard_api.settings import Settings
from loopguard_api.stream_tickets import StreamTicketRejected, StreamTicketService
from loopguard_api.subscriptions import SessionSubscriptionService


NOW = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)
ORIGIN = "http://localhost:3000"


def test_websocket_replays_then_streams_without_gap():
    tenant_id, user_id, session_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    tickets = StreamTicketService(clock=lambda: NOW)
    subscriptions = SessionSubscriptionService()
    subscriptions.register_session(tenant_id=tenant_id, session_id=session_id)
    for sequence in range(1, 4):
        subscriptions.publish(
            tenant_id=tenant_id,
            session_id=session_id,
            session_seq=sequence,
            payload={"value": sequence},
        )
    ticket = tickets.issue(
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=session_id,
        origin=ORIGIN,
        after_session_seq=2,
    )
    app = create_app(
        Settings.for_test(),
        stream_ticket_service=tickets,
        subscription_service=subscriptions,
    )

    with TestClient(app).websocket_connect(
        f"/v1/sessions/{session_id}/stream?ticket={ticket.ticket}",
        headers={"Origin": ORIGIN},
    ) as websocket:
        assert websocket.receive_json() == {
            "type": "event",
            "session_seq": 3,
            "client_stream_seq": 1,
            "payload": {"value": 3},
        }
        subscriptions.publish(
            tenant_id=tenant_id,
            session_id=session_id,
            session_seq=4,
            payload={"value": 4},
        )
        assert websocket.receive_json()["session_seq"] == 4


def test_ticket_is_hashed_one_use_expiring_and_origin_bound():
    current = NOW
    service = StreamTicketService(clock=lambda: current)
    values = {
        "tenant_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "session_id": uuid.uuid4(),
        "origin": ORIGIN,
        "after_session_seq": 7,
    }
    ticket = service.issue(**values)

    assert ticket.ticket not in service.debug_stored_values()
    consumed = service.consume(ticket.ticket, origin=ORIGIN)
    assert consumed.after_session_seq == 7
    with pytest.raises(StreamTicketRejected, match="consumed"):
        service.consume(ticket.ticket, origin=ORIGIN)

    wrong_origin = service.issue(**values)
    with pytest.raises(StreamTicketRejected, match="origin"):
        service.consume(wrong_origin.ticket, origin="https://evil.example")

    expired = service.issue(**values)
    current += timedelta(seconds=31)
    with pytest.raises(StreamTicketRejected, match="expired"):
        service.consume(expired.ticket, origin=ORIGIN)


def test_ticket_cannot_open_another_session_and_deleted_session_closes():
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    session_id, other_session = uuid.uuid4(), uuid.uuid4()
    tickets = StreamTicketService(clock=lambda: NOW)
    subscriptions = SessionSubscriptionService()
    subscriptions.register_session(tenant_id=tenant_id, session_id=session_id)
    app = create_app(
        Settings.for_test(),
        stream_ticket_service=tickets,
        subscription_service=subscriptions,
    )
    ticket = tickets.issue(
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=session_id,
        origin=ORIGIN,
        after_session_seq=0,
    )

    with pytest.raises(WebSocketDisconnect) as wrong:
        with TestClient(app).websocket_connect(
            f"/v1/sessions/{other_session}/stream?ticket={ticket.ticket}",
            headers={"Origin": ORIGIN},
        ):
            pass
    assert wrong.value.code == 4403

    valid = tickets.issue(
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=session_id,
        origin=ORIGIN,
        after_session_seq=0,
    )
    subscriptions.delete_session(tenant_id=tenant_id, session_id=session_id)
    with pytest.raises(WebSocketDisconnect) as deleted:
        with TestClient(app).websocket_connect(
            f"/v1/sessions/{session_id}/stream?ticket={valid.ticket}",
            headers={"Origin": ORIGIN},
        ):
            pass
    assert deleted.value.code == 4404


def test_native_bearer_upgrade_never_puts_access_token_in_url():
    tenant_id, user_id, session_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    subscriptions = SessionSubscriptionService()
    subscriptions.register_session(tenant_id=tenant_id, session_id=session_id)
    subscriptions.publish(
        tenant_id=tenant_id,
        session_id=session_id,
        session_seq=1,
        payload={"native": True},
    )

    class NativeAuth:
        async def authenticate(self, token: str) -> Principal:
            assert token == "native-access-token"
            return Principal(
                tenant_id=tenant_id,
                user_id=user_id,
                subject="native-user",
                role="viewer",
                permissions=frozenset({Permission.VIEW_SESSION}),
            )

    app = create_app(
        Settings.for_test(),
        auth_service=NativeAuth(),  # type: ignore[arg-type]
        subscription_service=subscriptions,
    )
    with TestClient(app).websocket_connect(
        f"/v1/sessions/{session_id}/stream?after_session_seq=0",
        headers={"Authorization": "Bearer native-access-token"},
    ) as websocket:
        assert websocket.receive_json()["session_seq"] == 1
