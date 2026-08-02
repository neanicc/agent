from __future__ import annotations

import re
import uuid
from datetime import timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from ..enterprise_identity import (
    EnterpriseIdentityRejected,
    EnterpriseIdentityService,
    ScimGroup,
    ScimIdentity,
    FederationConfig,
)
from ..authorization import Principal, require_policy_manager


SCIM_USER = "urn:ietf:params:scim:schemas:core:2.0:User"
SCIM_GROUP = "urn:ietf:params:scim:schemas:core:2.0:Group"
SCIM_LIST = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SCIM_PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
SCIM_ERROR = "urn:ietf:params:scim:api:messages:2.0:Error"
_USER_FILTER = re.compile(r'^userName\s+eq\s+"([^"\x00]{1,320})"$')


class ScimError(Exception):
    def __init__(self, status: int, detail: str, *, scim_type: str | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.scim_type = scim_type


class ScimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScimUserCreate(ScimRequest):
    schemas: list[str]
    externalId: str = Field(min_length=1, max_length=256)
    userName: str = Field(min_length=1, max_length=320)
    displayName: str = Field(min_length=1, max_length=256)
    active: bool = True


class ScimGroupCreate(ScimRequest):
    schemas: list[str]
    externalId: str = Field(min_length=1, max_length=256)
    displayName: str = Field(min_length=1, max_length=256)


class PatchOperation(ScimRequest):
    op: Literal["add", "replace", "remove"]
    path: str | None = Field(default=None, min_length=1, max_length=256)
    value: Any = None


class ScimPatchRequest(ScimRequest):
    schemas: list[str]
    Operations: list[PatchOperation] = Field(min_length=1, max_length=32)


class TokenCreate(ScimRequest):
    label: str = Field(min_length=1, max_length=128)
    lifetime_days: int = Field(default=90, ge=1, le=365)


class GroupMappingCreate(ScimRequest):
    external_group: str = Field(min_length=1, max_length=256)
    role: Literal["viewer", "operator", "admin", "owner"]


class DomainCreate(ScimRequest):
    domain: str = Field(min_length=3, max_length=253)


class FederationCreate(ScimRequest):
    provider: Literal["oidc", "saml"]
    issuer_or_entity_id: str = Field(min_length=1, max_length=2_048)
    verified_domain: str = Field(min_length=3, max_length=253)
    enabled: bool = True
    group_claim: str = Field(default="groups", min_length=1, max_length=128)


class ScimJSONResponse(JSONResponse):
    media_type = "application/scim+json"


router = APIRouter(
    prefix="/scim/v2",
    tags=["scim"],
    default_response_class=ScimJSONResponse,
)
admin_router = APIRouter(prefix="/v1/enterprise", tags=["enterprise"])


@admin_router.post("/scim-tokens", status_code=201)
async def issue_scim_token(
    request: Request,
    body: TokenCreate,
    principal: Annotated[Principal, Depends(require_policy_manager)],
) -> dict[str, object]:
    service: EnterpriseIdentityService = request.app.state.enterprise_identity_service
    try:
        record, token = service.issue_token(
            principal.tenant_id,
            label=body.label,
            lifetime=timedelta(days=body.lifetime_days),
        )
    except EnterpriseIdentityRejected as exc:
        raise ScimError(400, str(exc)) from exc
    return {
        "id": str(record.id),
        "label": record.label,
        "created_at": record.created_at.isoformat(),
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "token": token,
        "notice": "Copy this token now. LoopGuard stores only its salted hash.",
    }


@admin_router.delete("/scim-tokens/{token_id}", status_code=204)
async def revoke_scim_token(
    token_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require_policy_manager)],
) -> Response:
    service: EnterpriseIdentityService = request.app.state.enterprise_identity_service
    try:
        service.revoke_token(principal.tenant_id, token_id)
    except EnterpriseIdentityRejected as exc:
        raise ScimError(404, "SCIM token not found") from exc
    return Response(status_code=204)


@admin_router.put("/group-mappings/{external_group}")
async def put_group_mapping(
    external_group: str,
    request: Request,
    body: GroupMappingCreate,
    principal: Annotated[Principal, Depends(require_policy_manager)],
) -> dict[str, str]:
    if body.external_group != external_group:
        raise ScimError(400, "Path and body group names must match")
    service: EnterpriseIdentityService = request.app.state.enterprise_identity_service
    try:
        service.map_group(principal.tenant_id, external_group, body.role)
    except EnterpriseIdentityRejected as exc:
        raise ScimError(400, str(exc)) from exc
    return {"external_group": external_group, "role": body.role}


@admin_router.post("/domains/challenge", status_code=201)
async def create_domain_challenge(
    request: Request,
    body: DomainCreate,
    principal: Annotated[Principal, Depends(require_policy_manager)],
) -> dict[str, str]:
    service: EnterpriseIdentityService = request.app.state.enterprise_identity_service
    try:
        challenge = service.create_domain_challenge(principal.tenant_id, body.domain)
    except EnterpriseIdentityRejected as exc:
        raise ScimError(400, str(exc)) from exc
    return {
        "domain": challenge.domain,
        "record_name": challenge.domain,
        "record_type": "TXT",
        "record_value": challenge.expected_record,
        "expires_at": challenge.expires_at.isoformat(),
    }


@admin_router.post("/domains/{domain}/verify")
async def verify_domain(
    domain: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_policy_manager)],
) -> dict[str, str]:
    service: EnterpriseIdentityService = request.app.state.enterprise_identity_service
    try:
        verified = service.verify_domain(principal.tenant_id, domain)
    except EnterpriseIdentityRejected as exc:
        raise ScimError(409, str(exc)) from exc
    return {
        "domain": verified.domain,
        "verified_at": verified.verified_at.isoformat() if verified.verified_at else "",
    }


@admin_router.put("/federation")
async def configure_federation(
    request: Request,
    body: FederationCreate,
    principal: Annotated[Principal, Depends(require_policy_manager)],
) -> dict[str, object]:
    service: EnterpriseIdentityService = request.app.state.enterprise_identity_service
    try:
        config = FederationConfig(
            tenant_id=principal.tenant_id,
            **body.model_dump(),
        )
        service.configure_federation(config)
    except (EnterpriseIdentityRejected, ValueError) as exc:
        raise ScimError(400, str(exc)) from exc
    return {
        "provider": config.provider,
        "issuer_or_entity_id": config.issuer_or_entity_id,
        "verified_domain": config.verified_domain,
        "enabled": config.enabled,
        "group_claim": config.group_claim,
    }


@router.get("/ServiceProviderConfig")
async def service_provider_config() -> dict[str, object]:
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": 200},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [
            {
                "type": "oauthbearertoken",
                "name": "Bearer Token",
                "description": "Tenant-scoped, hashed, rotatable SCIM bearer token.",
                "specUri": "https://www.rfc-editor.org/rfc/rfc6750",
                "primary": True,
            }
        ],
    }


@router.get("/ResourceTypes")
async def resource_types() -> dict[str, object]:
    return {
        "schemas": [SCIM_LIST],
        "totalResults": 2,
        "startIndex": 1,
        "itemsPerPage": 2,
        "Resources": [
            {
                "id": "User",
                "name": "User",
                "endpoint": "/Users",
                "schema": SCIM_USER,
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
            },
            {
                "id": "Group",
                "name": "Group",
                "endpoint": "/Groups",
                "schema": SCIM_GROUP,
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
            },
        ],
    }


@router.get("/Schemas")
async def schemas() -> dict[str, object]:
    return {
        "schemas": [SCIM_LIST],
        "totalResults": 2,
        "startIndex": 1,
        "itemsPerPage": 2,
        "Resources": [
            {"id": SCIM_USER, "name": "User", "schemas": [SCIM_USER], "attributes": []},
            {"id": SCIM_GROUP, "name": "Group", "schemas": [SCIM_GROUP], "attributes": []},
        ],
    }


@router.get("/Users")
async def list_users(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    filter: str | None = None,
    start_index: int = Query(1, alias="startIndex", ge=1),
    count: int = Query(100, ge=1, le=200),
) -> dict[str, object]:
    service, tenant_id = _context(request, authorization)
    user_name = None
    if filter is not None:
        match = _USER_FILTER.fullmatch(filter)
        if match is None:
            raise ScimError(400, "Only exact userName eq filters are supported", scim_type="invalidFilter")
        user_name = match.group(1)
    users = service.list_users(tenant_id, user_name=user_name)
    page = users[start_index - 1 : start_index - 1 + count]
    return _list([_user(value) for value in page], len(users), start_index)


@router.post("/Users", status_code=201)
async def create_user(
    request: Request,
    response: Response,
    body: ScimUserCreate,
    authorization: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, object]:
    _require_schema(body.schemas, SCIM_USER)
    service, tenant_id = _context(request, authorization)
    try:
        identity = service.create_user(
            tenant_id,
            external_id=body.externalId,
            user_name=body.userName,
            display_name=body.displayName,
            active=body.active,
            idempotency_key=_idempotency(idempotency_key),
        )
    except EnterpriseIdentityRejected as exc:
        raise _translate(exc)
    response.headers["Location"] = f"/scim/v2/Users/{identity.id}"
    return _user(identity)


@router.get("/Users/{user_id}")
async def read_user(
    user_id: uuid.UUID,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    service, tenant_id = _context(request, authorization)
    try:
        return _user(service.read_user(tenant_id, user_id))
    except EnterpriseIdentityRejected as exc:
        raise ScimError(404, "User not found") from exc


@router.patch("/Users/{user_id}")
async def patch_user(
    user_id: uuid.UUID,
    request: Request,
    body: ScimPatchRequest,
    authorization: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, object]:
    _require_schema(body.schemas, SCIM_PATCH)
    service, tenant_id = _context(request, authorization)
    active: bool | None = None
    display_name: str | None = None
    for operation in body.Operations:
        path = (operation.path or "").casefold()
        if operation.op not in {"add", "replace"}:
            raise ScimError(400, "User PATCH only supports add or replace", scim_type="invalidSyntax")
        if path == "active" and isinstance(operation.value, bool):
            active = operation.value
        elif path == "displayname" and isinstance(operation.value, str):
            display_name = operation.value
        else:
            raise ScimError(400, "Unsupported User PATCH path or value", scim_type="invalidPath")
    try:
        return _user(
            service.update_user(
                tenant_id,
                user_id,
                active=active,
                display_name=display_name,
                group_ids=None,
                idempotency_key=_idempotency(idempotency_key),
            )
        )
    except EnterpriseIdentityRejected as exc:
        raise _translate(exc)


@router.delete("/Users/{user_id}", status_code=204)
async def delete_user(
    user_id: uuid.UUID,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Response:
    service, tenant_id = _context(request, authorization)
    try:
        service.update_user(
            tenant_id,
            user_id,
            active=False,
            display_name=None,
            group_ids=None,
            idempotency_key=_idempotency(idempotency_key),
        )
    except EnterpriseIdentityRejected as exc:
        raise _translate(exc)
    return Response(status_code=204)


@router.get("/Groups")
async def list_groups(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    start_index: int = Query(1, alias="startIndex", ge=1),
    count: int = Query(100, ge=1, le=200),
) -> dict[str, object]:
    service, tenant_id = _context(request, authorization)
    groups = service.list_groups(tenant_id)
    page = groups[start_index - 1 : start_index - 1 + count]
    return _list([_group(value) for value in page], len(groups), start_index)


@router.post("/Groups", status_code=201)
async def create_group(
    request: Request,
    response: Response,
    body: ScimGroupCreate,
    authorization: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, object]:
    _require_schema(body.schemas, SCIM_GROUP)
    service, tenant_id = _context(request, authorization)
    try:
        group = service.create_group(
            tenant_id,
            external_id=body.externalId,
            display_name=body.displayName,
            idempotency_key=_idempotency(idempotency_key),
        )
    except EnterpriseIdentityRejected as exc:
        raise _translate(exc)
    response.headers["Location"] = f"/scim/v2/Groups/{group.id}"
    return _group(group)


@router.get("/Groups/{group_id}")
async def read_group(
    group_id: uuid.UUID,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    service, tenant_id = _context(request, authorization)
    try:
        return _group(service.read_group(tenant_id, group_id))
    except EnterpriseIdentityRejected as exc:
        raise ScimError(404, "Group not found") from exc


@router.patch("/Groups/{group_id}")
async def patch_group(
    group_id: uuid.UUID,
    request: Request,
    body: ScimPatchRequest,
    authorization: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, object]:
    _require_schema(body.schemas, SCIM_PATCH)
    service, tenant_id = _context(request, authorization)
    member_ids: list[uuid.UUID] | None = None
    active: bool | None = None
    for operation in body.Operations:
        path = (operation.path or "").casefold()
        if operation.op not in {"add", "replace"}:
            raise ScimError(400, "Group PATCH only supports add or replace", scim_type="invalidSyntax")
        if path == "active" and isinstance(operation.value, bool):
            active = operation.value
        elif path == "members" and isinstance(operation.value, list):
            try:
                member_ids = [
                    uuid.UUID(str(value["value"]))
                    for value in operation.value
                    if isinstance(value, dict) and set(value) <= {"value", "display"}
                ]
            except (KeyError, ValueError, TypeError) as exc:
                raise ScimError(400, "Group members are invalid", scim_type="invalidValue") from exc
            if len(member_ids) != len(operation.value):
                raise ScimError(400, "Group members are invalid", scim_type="invalidValue")
        else:
            raise ScimError(400, "Unsupported Group PATCH path or value", scim_type="invalidPath")
    try:
        return _group(
            service.update_group(
                tenant_id,
                group_id,
                member_ids=member_ids,
                active=active,
                idempotency_key=_idempotency(idempotency_key),
            )
        )
    except EnterpriseIdentityRejected as exc:
        raise _translate(exc)


@router.delete("/Groups/{group_id}", status_code=204)
async def delete_group(
    group_id: uuid.UUID,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Response:
    service, tenant_id = _context(request, authorization)
    try:
        service.update_group(
            tenant_id,
            group_id,
            member_ids=[],
            active=False,
            idempotency_key=_idempotency(idempotency_key),
        )
    except EnterpriseIdentityRejected as exc:
        raise _translate(exc)
    return Response(status_code=204)


def _context(
    request: Request,
    authorization: str | None,
) -> tuple[EnterpriseIdentityService, uuid.UUID]:
    if authorization is None or not authorization.startswith("Bearer "):
        raise ScimError(401, "Bearer authentication is required")
    token = authorization.removeprefix("Bearer ").strip()
    service: EnterpriseIdentityService = request.app.state.enterprise_identity_service
    try:
        return service, service.authenticate(token)
    except EnterpriseIdentityRejected as exc:
        raise ScimError(401, "Bearer authentication failed") from exc


def _require_schema(values: list[str], expected: str) -> None:
    if values != [expected]:
        raise ScimError(400, "SCIM schemas are invalid", scim_type="invalidSyntax")


def _idempotency(value: str | None) -> str:
    if value is None:
        raise ScimError(400, "Idempotency-Key is required", scim_type="invalidSyntax")
    return value


def _translate(exc: EnterpriseIdentityRejected) -> ScimError:
    detail = str(exc)
    if "does not exist" in detail:
        return ScimError(404, detail)
    if "already exists" in detail:
        return ScimError(409, detail, scim_type="uniqueness")
    if "idempotency" in detail:
        return ScimError(409, detail, scim_type="mutability")
    return ScimError(400, detail, scim_type="invalidValue")


def _user(value: ScimIdentity) -> dict[str, object]:
    return {
        "schemas": [SCIM_USER],
        "id": str(value.id),
        "externalId": value.external_id,
        "userName": value.user_name,
        "displayName": value.display_name,
        "active": value.active,
        "groups": [{"value": str(group_id)} for group_id in value.group_ids],
        "meta": {
            "resourceType": "User",
            "created": value.created_at.isoformat(),
            "lastModified": value.updated_at.isoformat(),
            "location": f"/scim/v2/Users/{value.id}",
        },
    }


def _group(value: ScimGroup) -> dict[str, object]:
    return {
        "schemas": [SCIM_GROUP],
        "id": str(value.id),
        "externalId": value.external_id,
        "displayName": value.display_name,
        "members": [{"value": str(user_id)} for user_id in value.member_ids],
        "meta": {
            "resourceType": "Group",
            "created": value.created_at.isoformat(),
            "lastModified": value.updated_at.isoformat(),
            "location": f"/scim/v2/Groups/{value.id}",
        },
    }


def _list(resources: list[dict[str, object]], total: int, start: int) -> dict[str, object]:
    return {
        "schemas": [SCIM_LIST],
        "totalResults": total,
        "startIndex": start,
        "itemsPerPage": len(resources),
        "Resources": resources,
    }


def error_body(error: ScimError) -> dict[str, object]:
    body: dict[str, object] = {
        "schemas": [SCIM_ERROR],
        "status": str(error.status),
        "detail": error.detail,
    }
    if error.scim_type is not None:
        body["scimType"] = error.scim_type
    return body
