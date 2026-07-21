from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import event, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session as SyncSession
from sqlalchemy.orm import with_loader_criteria
from sqlalchemy.sql import visitors
from sqlalchemy.sql.schema import Table

from .models import (
    CLOUD_INGEST_SEQUENCE,
    Event,
    RelayOutbox,
    Repository,
    Session,
    Tenant,
    TenantOwnedMixin,
)


@dataclass(frozen=True, slots=True)
class TenantContext:
    tenant_id: uuid.UUID

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, uuid.UUID):
            raise ValueError("tenant context requires a UUID")


class TenantSession(SyncSession):
    """ORM session that cannot exist without an immutable tenant context."""

    def __init__(self, *args: Any, tenant_context: TenantContext | None = None, **kwargs: Any) -> None:
        if tenant_context is None:
            raise ValueError("tenant context is required")
        self.tenant_context = tenant_context
        super().__init__(*args, **kwargs)
        self.info["tenant_context"] = tenant_context


@event.listens_for(TenantSession, "do_orm_execute")
def _scope_orm_query(execute_state: Any) -> None:
    tenant_id = execute_state.session.tenant_context.tenant_id
    if execute_state.execution_options.get("_loopguard_internal_global") is True:
        return
    if execute_state.is_insert:
        if any("tenant_id" in table.c for table in _tables(execute_state.statement)):
            raise ValueError("bulk tenant inserts are not allowed")
        return
    if not (execute_state.is_select or execute_state.is_update or execute_state.is_delete):
        return
    statement = execute_state.statement
    if execute_state.is_select:
        statement = statement.options(
            with_loader_criteria(
                TenantOwnedMixin,
                lambda model: model.tenant_id == tenant_id,
                include_aliases=True,
            )
        ).options(
            with_loader_criteria(Tenant, lambda model: model.id == tenant_id)
        )
    if execute_state.is_update and _updates_tenant_id(statement):
        raise ValueError("tenant identity cannot be changed by a bulk update")
    for table in _tables(statement):
        if "tenant_id" in table.c:
            statement = statement.where(table.c.tenant_id == tenant_id)
        elif table.name == Tenant.__tablename__:
            statement = statement.where(table.c.id == tenant_id)
    execute_state.statement = statement


@event.listens_for(TenantSession, "before_flush")
def _scope_writes(session: TenantSession, _flush_context: Any, _instances: Any) -> None:
    tenant_id = session.tenant_context.tenant_id
    for item in {*session.new, *session.dirty, *session.deleted}:
        if isinstance(item, TenantOwnedMixin) and item.tenant_id != tenant_id:
            raise ValueError("tenant context mismatch")
        if isinstance(item, Tenant) and item.id != tenant_id:
            raise ValueError("tenant context mismatch")


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(connection: Any, _record: Any) -> None:
    if connection.__class__.__module__.startswith("sqlite3"):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


TenantSessionFactory = async_sessionmaker[AsyncSession]


def create_database_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    if not database_url.startswith(("postgresql+asyncpg://", "sqlite+aiosqlite://")):
        raise ValueError("database URL must use asyncpg or aiosqlite")
    return create_async_engine(
        database_url,
        echo=echo,
        pool_pre_ping=True,
        connect_args={"timeout": 15} if database_url.startswith("sqlite") else {},
    )


def tenant_session_factory(engine: AsyncEngine) -> TenantSessionFactory:
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        sync_session_class=TenantSession,
        expire_on_commit=False,
        autoflush=False,
    )


@asynccontextmanager
async def tenant_transaction(
    factory: TenantSessionFactory,
    context: TenantContext,
) -> AsyncIterator[AsyncSession]:
    async with factory(tenant_context=context) as session:
        async with session.begin():
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                await session.execute(
                    text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                    {"tenant_id": str(context.tenant_id)},
                )
            yield session


async def create_repository(
    database: AsyncSession,
    context: TenantContext,
    *,
    name: str,
    host_id: uuid.UUID | None = None,
    canonical_identity: str | None = None,
) -> Repository:
    _assert_context(database, context)
    normalized_name = _text(name, 256, "repository name")
    identity = canonical_identity or f"local:{uuid.uuid4()}"
    repository = Repository(
        tenant_id=context.tenant_id,
        host_id=host_id,
        name=normalized_name,
        canonical_identity=_text(identity, 512, "repository identity"),
        repository_handle=f"rh_{secrets.token_urlsafe(32)}",
        relay_scopes=["events:write", "actions:receive"],
    )
    database.add(repository)
    await database.flush()
    return repository


async def list_repositories(
    database: AsyncSession,
    context: TenantContext,
) -> list[Repository]:
    _assert_context(database, context)
    return list(
        (
            await database.scalars(
                select(Repository).order_by(Repository.created_at, Repository.id)
            )
        ).all()
    )


async def ingest_event(
    database: AsyncSession,
    context: TenantContext,
    *,
    host_id: uuid.UUID,
    repository_id: uuid.UUID,
    session_id: uuid.UUID,
    event_id: str,
    kind: str,
    payload: dict[str, Any],
    local_log_seq: int | None,
    repo_seq: int | None,
) -> Event:
    _assert_context(database, context)
    normalized_event_id = _text(event_id, 256, "event ID")
    normalized_kind = _text(kind, 128, "event kind")
    _sequence(local_log_seq, "local log sequence")
    _sequence(repo_seq, "repository sequence")
    if not isinstance(payload, dict):
        raise ValueError("event payload must be an object")

    if database.bind is not None and database.bind.dialect.name == "postgresql":
        lock = int.from_bytes(
            hashlib.sha256(f"{context.tenant_id}:{normalized_event_id}".encode()).digest()[:8],
            "big",
            signed=True,
        )
        await database.execute(text("SELECT pg_advisory_xact_lock(:lock)"), {"lock": lock})

    existing = await database.scalar(
        select(Event).where(Event.event_id == normalized_event_id)
    )
    if existing is not None:
        _same_event(
            existing,
            host_id=host_id,
            repository_id=repository_id,
            session_id=session_id,
            kind=normalized_kind,
            local_log_seq=local_log_seq,
            repo_seq=repo_seq,
            payload=payload,
        )
        return existing

    session = await database.scalar(
        select(Session).where(Session.id == session_id).with_for_update()
    )
    if (
        session is None
        or session.tenant_id != context.tenant_id
        or session.host_id != host_id
        or session.repository_id != repository_id
    ):
        raise ValueError("event topology is not bound to the tenant session")
    session_seq = session.next_session_seq
    session.next_session_seq += 1
    cloud_ingest_seq = await _next_cloud_ingest_seq(database)
    model = Event(
        tenant_id=context.tenant_id,
        event_id=normalized_event_id,
        host_id=host_id,
        repository_id=repository_id,
        session_id=session_id,
        cloud_ingest_seq=cloud_ingest_seq,
        session_seq=session_seq,
        local_log_seq=local_log_seq,
        repo_seq=repo_seq,
        client_stream_seq=None,
        kind=normalized_kind,
        payload=payload,
    )
    database.add(model)
    await database.flush()
    database.add(
        RelayOutbox(
            tenant_id=context.tenant_id,
            event_id=model.id,
            session_id=session_id,
            cloud_ingest_seq=cloud_ingest_seq,
            payload={
                "event_id": normalized_event_id,
                "session_id": str(session_id),
                "session_seq": session_seq,
                "cloud_ingest_seq": cloud_ingest_seq,
            },
        )
    )
    await database.flush()
    return model


async def _next_cloud_ingest_seq(database: AsyncSession) -> int:
    if database.bind is not None and database.bind.dialect.name == "postgresql":
        value = await database.scalar(select(CLOUD_INGEST_SEQUENCE.next_value()))
    else:
        value = (
            await database.scalar(
                select(func.max(Event.__table__.c.cloud_ingest_seq)).execution_options(
                    _loopguard_internal_global=True
                )
            )
        ) or 0
        value += 1
    if not isinstance(value, int) or value < 1:
        raise RuntimeError("database returned an invalid cloud ingest sequence")
    return value


def _assert_context(database: AsyncSession, context: TenantContext) -> None:
    active = database.sync_session.info.get("tenant_context")
    if not isinstance(active, TenantContext) or active != context:
        raise ValueError("tenant context mismatch")


def _same_event(
    event: Event,
    *,
    host_id: uuid.UUID,
    repository_id: uuid.UUID,
    session_id: uuid.UUID,
    kind: str,
    local_log_seq: int | None,
    repo_seq: int | None,
    payload: dict[str, Any],
) -> None:
    if (
        event.host_id != host_id
        or event.repository_id != repository_id
        or event.session_id != session_id
        or event.kind != kind
        or event.local_log_seq != local_log_seq
        or event.repo_seq != repo_seq
        or event.payload != payload
    ):
        raise ValueError("duplicate event ID has conflicting provenance")


def _sequence(value: int | None, label: str) -> None:
    if value is not None and (
        not isinstance(value, int) or isinstance(value, bool) or value < 0
    ):
        raise ValueError(f"{label} must be non-negative")


def _text(value: str, maximum: int, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum or "\x00" in normalized:
        raise ValueError(f"{label} must be bounded and non-empty")
    return normalized


def _tables(statement: Any) -> set[Table]:
    return {
        element
        for element in visitors.iterate(statement)
        if isinstance(element, Table)
    }


def _updates_tenant_id(statement: Any) -> bool:
    values = getattr(statement, "_values", None)
    if not isinstance(values, Mapping):
        return False
    return any(getattr(column, "name", column) == "tenant_id" for column in values)
