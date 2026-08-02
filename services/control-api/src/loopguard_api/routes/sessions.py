from __future__ import annotations

import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field

from ..authorization import Permission, Principal, require_viewer
from ..capacity import CapacityExceeded, CapacityLimiter
from ..stream_tickets import StreamGrant, StreamTicketRejected, StreamTicketService
from ..subscriptions import SessionSubscriptionService


router = APIRouter(prefix="/v1", tags=["sessions"])


class StreamTicketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: uuid.UUID
    after_session_seq: int = Field(default=0, ge=0)


@router.get("/sessions")
async def list_sessions(
    request: Request,
    principal: Annotated[Principal, Depends(require_viewer)],
) -> dict[str, object]:
    service = request.app.state.control_queries
    return {"items": service.resources("sessions", principal.tenant_id), "next_cursor": None}


@router.get("/sessions/{session_id}")
async def read_session(
    request: Request,
    session_id: uuid.UUID,
    principal: Annotated[Principal, Depends(require_viewer)],
) -> dict[str, object]:
    service = request.app.state.control_queries
    value = service.resource("sessions", principal.tenant_id, session_id)
    if value is None:
        from ..errors import ApiProblem

        raise ApiProblem("LGAPI-NOT-FOUND")
    return value


@router.post("/stream-tickets", status_code=201)
async def create_stream_ticket(
    request: Request,
    body: StreamTicketRequest,
    principal: Annotated[Principal, Depends(require_viewer)],
    origin: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    allowed_origin = origin if origin in request.app.state.settings.allowed_origins else None
    if allowed_origin is None:
        from ..errors import ApiProblem

        raise ApiProblem("LGAPI-ORIGIN-DENIED")
    subscriptions: SessionSubscriptionService = request.app.state.subscription_service
    if not subscriptions.exists(
        tenant_id=principal.tenant_id, session_id=body.session_id
    ):
        from ..errors import ApiProblem

        raise ApiProblem("LGAPI-NOT-FOUND")
    tickets: StreamTicketService = request.app.state.stream_ticket_service
    issued = tickets.issue(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        session_id=body.session_id,
        origin=allowed_origin,
        after_session_seq=body.after_session_seq,
    )
    return {"ticket": issued.ticket, "expires_at": issued.expires_at.isoformat()}


@router.websocket("/sessions/{session_id}/stream")
async def session_stream(websocket: WebSocket, session_id: uuid.UUID) -> None:
    tickets: StreamTicketService = websocket.app.state.stream_ticket_service
    subscriptions: SessionSubscriptionService = websocket.app.state.subscription_service
    ticket = websocket.query_params.get("ticket")
    if ticket is not None:
        origin = websocket.headers.get("origin", "")
        try:
            grant = tickets.consume(ticket, origin=origin)
        except StreamTicketRejected:
            await websocket.close(code=4401)
            return
    else:
        authorization = websocket.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        try:
            after_session_seq = int(
                websocket.query_params.get("after_session_seq", "0")
            )
            if scheme.lower() != "bearer" or not token or after_session_seq < 0:
                raise ValueError
            principal = await websocket.app.state.auth_service.authenticate(token)
            if Permission.VIEW_SESSION not in principal.permissions:
                raise PermissionError
            grant = StreamGrant(
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                session_id=session_id,
                origin="native-bearer",
                after_session_seq=after_session_seq,
            )
        except PermissionError:
            await websocket.close(code=4403)
            return
        except Exception:
            await websocket.close(code=4401)
            return
    if grant.session_id != session_id:
        await websocket.close(code=4403)
        return
    if not subscriptions.exists(tenant_id=grant.tenant_id, session_id=session_id):
        await websocket.close(code=4404)
        return

    limiter: CapacityLimiter = websocket.app.state.capacity_limiter
    try:
        with limiter.stream(grant.tenant_id):
            await _serve_session_stream(websocket, grant, subscriptions)
    except CapacityExceeded:
        await websocket.close(code=4429, reason="tenant stream capacity exhausted")


async def _serve_session_stream(
    websocket: WebSocket,
    grant: StreamGrant,
    subscriptions: SessionSubscriptionService,
) -> None:
    await websocket.accept()
    session_id = grant.session_id
    last_session_seq = grant.after_session_seq
    client_stream_seq = 0
    try:
        while True:
            events = subscriptions.read_after(
                tenant_id=grant.tenant_id,
                session_id=session_id,
                after_session_seq=last_session_seq,
            )
            if events is None:
                await websocket.close(code=4404)
                return
            if events:
                for event in events:
                    client_stream_seq += 1
                    async with asyncio.timeout(5):
                        await websocket.send_json(
                            {
                                "type": "event",
                                "session_seq": event.session_seq,
                                "client_stream_seq": client_stream_seq,
                                "payload": event.payload,
                            }
                        )
                    last_session_seq = event.session_seq
                continue
            await subscriptions.wait_for_change(
                tenant_id=grant.tenant_id,
                session_id=session_id,
                after_session_seq=last_session_seq,
                timeout=15,
            )
            events = subscriptions.read_after(
                tenant_id=grant.tenant_id,
                session_id=session_id,
                after_session_seq=last_session_seq,
            )
            if events == []:
                await websocket.send_json(
                    {
                        "type": "heartbeat",
                        "last_session_seq": last_session_seq,
                        "last_client_stream_seq": client_stream_seq,
                    }
                )
    except (WebSocketDisconnect, TimeoutError):
        return
