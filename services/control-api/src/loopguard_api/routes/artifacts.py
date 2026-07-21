from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, status
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from ..artifacts import ArtifactIntegrityError, ArtifactNotFound, ArtifactService
from ..authorization import Principal, require_controller, require_viewer
from ..errors import ApiProblem


router = APIRouter(prefix="/v1/artifacts", tags=["artifacts"])


class ArtifactInitiation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(ge=1, le=5 * 1024 * 1024 * 1024)
    media_type: str = Field(min_length=1, max_length=256)
    retention_class: Literal["source", "log", "proof", "repair"]
    expires_at: AwareDatetime | None = None


class ArtifactCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_manifest: str = Field(min_length=1, max_length=4096)


@router.post("", status_code=status.HTTP_201_CREATED)
async def initiate_artifact(
    request: Request,
    body: ArtifactInitiation,
    principal: Annotated[Principal, Depends(require_controller)],
) -> dict[str, object]:
    service: ArtifactService = request.app.state.artifact_service
    try:
        artifact, upload = service.initiate(
            tenant_id=principal.tenant_id,
            declared_sha256=body.sha256,
            byte_count=body.byte_count,
            media_type=body.media_type,
            retention_class=body.retention_class,
            expires_at=body.expires_at,
        )
    except ArtifactIntegrityError as exc:
        raise ApiProblem("LGAPI-ARTIFACT-INTEGRITY") from exc
    return {
        "artifact_id": str(artifact.id),
        "state": artifact.state,
        "upload_url": upload.url,
        "upload_expires_at": upload.expires_at,
        "required_headers": {
            "x-amz-checksum-sha256": upload.required_sha256,
            "content-type": upload.required_media_type,
            "content-length": str(upload.required_byte_count),
        },
    }


@router.post("/{artifact_id}/complete")
async def complete_artifact(
    request: Request,
    artifact_id: uuid.UUID,
    body: ArtifactCompletion,
    principal: Annotated[Principal, Depends(require_controller)],
) -> dict[str, str]:
    service: ArtifactService = request.app.state.artifact_service
    try:
        artifact = service.complete(
            tenant_id=principal.tenant_id,
            artifact_id=artifact_id,
            evidence_manifest=body.evidence_manifest,
        )
    except ArtifactNotFound as exc:
        raise ApiProblem("LGAPI-NOT-FOUND") from exc
    except ArtifactIntegrityError as exc:
        raise ApiProblem("LGAPI-ARTIFACT-INTEGRITY") from exc
    return {"artifact_id": str(artifact.id), "state": artifact.state}


@router.get("/{artifact_id}")
async def download_artifact(
    request: Request,
    artifact_id: uuid.UUID,
    principal: Annotated[Principal, Depends(require_viewer)],
) -> dict[str, str | datetime]:
    service: ArtifactService = request.app.state.artifact_service
    try:
        url = service.download(
            tenant_id=principal.tenant_id, artifact_id=artifact_id
        )
    except ArtifactNotFound as exc:
        raise ApiProblem("LGAPI-NOT-FOUND") from exc
    return {"download_url": url}
