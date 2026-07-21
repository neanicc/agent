from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .db import TenantContext
from .errors import ApiProblem


class Permission(StrEnum):
    VIEW_SESSION = "session:view"
    CONTROL_SESSION = "session:control"
    MANAGE_POLICY = "policy:manage"
    RUN_REPAIR = "repair:run"
    PUBLISH_REPAIR = "repair:publish"
    MANAGE_DEVICES = "device:manage"


ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    "viewer": frozenset({Permission.VIEW_SESSION}),
    "operator": frozenset(
        {Permission.VIEW_SESSION, Permission.CONTROL_SESSION, Permission.RUN_REPAIR}
    ),
    "admin": frozenset(Permission),
    "owner": frozenset(Permission),
}


@dataclass(frozen=True, slots=True)
class Principal:
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    subject: str
    role: str
    permissions: frozenset[Permission]

    @property
    def tenant_context(self) -> TenantContext:
        return TenantContext(self.tenant_id)


_bearer = HTTPBearer(auto_error=False)


async def current_principal(
    request: Request,
    credential: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credential is None or credential.scheme.lower() != "bearer":
        raise ApiProblem("LGAPI-UNAUTHORIZED")
    auth_service: Any = getattr(request.app.state, "auth_service", None)
    if auth_service is None:
        raise ApiProblem("LGAPI-UNAUTHORIZED")
    try:
        return await auth_service.authenticate(credential.credentials)
    except ApiProblem:
        raise
    except Exception as exc:
        raise ApiProblem("LGAPI-UNAUTHORIZED") from exc


def require_permission(permission: Permission):
    async def dependency(
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> Principal:
        if permission not in principal.permissions:
            raise ApiProblem("LGAPI-FORBIDDEN")
        return principal

    return dependency


require_viewer = require_permission(Permission.VIEW_SESSION)
require_controller = require_permission(Permission.CONTROL_SESSION)
require_policy_manager = require_permission(Permission.MANAGE_POLICY)
require_repair_runner = require_permission(Permission.RUN_REPAIR)
require_repair_publisher = require_permission(Permission.PUBLISH_REPAIR)
require_device_manager = require_permission(Permission.MANAGE_DEVICES)
