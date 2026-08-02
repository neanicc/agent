from __future__ import annotations

from fastapi import APIRouter, Header, Request, status

from ..errors import ApiProblem
from ..hook_ingest import HookRejected, HookService


router = APIRouter(prefix="/v1", tags=["hooks"])


@router.post("/hook-events", status_code=status.HTTP_202_ACCEPTED)
async def ingest_hook_event(
    request: Request,
    x_loopguard_key_id: str = Header(),
    x_loopguard_timestamp: str = Header(),
    x_loopguard_nonce: str = Header(),
    x_loopguard_repository: str = Header(),
    x_loopguard_signature: str = Header(),
) -> dict[str, str]:
    body = await request.body()
    service: HookService = request.app.state.hook_service
    try:
        verified = service.verify(
            method=request.method,
            path=request.url.path,
            body=body,
            key_id=x_loopguard_key_id,
            timestamp=x_loopguard_timestamp,
            nonce=x_loopguard_nonce,
            repository_handle=x_loopguard_repository,
            signature=x_loopguard_signature,
        )
    except HookRejected as exc:
        message = str(exc)
        if "replay" in message:
            code = "LGAPI-HOOK-REPLAY"
        elif "repository binding" in message:
            code = "LGAPI-HOOK-BINDING"
        else:
            code = "LGAPI-HOOK-INVALID"
        raise ApiProblem(code) from exc
    return {"status": "accepted", "repository_handle": verified.repository_handle}
