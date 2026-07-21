from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, insert, select, update

from loopguard_api.db import (
    TenantContext,
    create_database_engine,
    create_repository,
    ingest_event,
    list_repositories,
    tenant_session_factory,
    tenant_transaction,
)
from loopguard_api.models import (
    Base,
    Event,
    Host,
    RelayOutbox,
    Repository,
    Session,
    Tenant,
)


def run(coroutine):
    return asyncio.run(coroutine)


def test_repository_query_is_tenant_scoped(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, factory, tenant_a, tenant_b = await database(tmp_path)
        try:
            async with tenant_transaction(factory, tenant_a) as db:
                repo = await create_repository(db, tenant_a, name="private")
                repo_id = repo.id
            async with tenant_transaction(factory, tenant_a) as db:
                assert [repo.id for repo in await list_repositories(db, tenant_a)] == [repo_id]
            async with tenant_transaction(factory, tenant_b) as db:
                assert await list_repositories(db, tenant_b) == []
                assert (await db.scalars(select(Repository))).all() == []
                assert await db.scalar(select(func.count(Repository.id))) == 0
                assert (await db.execute(select(Repository.__table__))).all() == []
                assert [tenant.id for tenant in (await db.scalars(select(Tenant))).all()] == [
                    tenant_b.tenant_id
                ]
        finally:
            await engine.dispose()

    run(scenario())


def test_direct_and_bulk_writes_cannot_cross_tenant_scope(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, factory, tenant_a, tenant_b = await database(tmp_path)
        try:
            async with tenant_transaction(factory, tenant_a) as db:
                repository = await create_repository(db, tenant_a, name="tenant-a")
                repository_id = repository.id
            async with tenant_transaction(factory, tenant_b) as db:
                await db.execute(
                    update(Repository)
                    .where(Repository.id == repository_id)
                    .values(name="stolen")
                )
                with pytest.raises(ValueError, match="tenant identity"):
                    await db.execute(
                        update(Repository)
                        .where(Repository.id == repository_id)
                        .values(tenant_id=tenant_b.tenant_id)
                    )
                with pytest.raises(ValueError, match="bulk tenant inserts"):
                    await db.execute(
                        insert(Repository),
                        [{
                            "tenant_id": tenant_a.tenant_id,
                            "name": "injected",
                            "canonical_identity": "injected",
                            "repository_handle": "rh_injected",
                            "relay_scopes": [],
                        }],
                    )
                db.add(
                    Repository(
                        tenant_id=tenant_a.tenant_id,
                        name="injected",
                        canonical_identity="injected-orm",
                        repository_handle="rh_injected_orm",
                        relay_scopes=[],
                    )
                )
                with pytest.raises(ValueError, match="tenant context mismatch"):
                    await db.flush()
                await db.rollback()
            async with tenant_transaction(factory, tenant_a) as db:
                assert (await db.get(Repository, repository_id)).name == "tenant-a"
        finally:
            await engine.dispose()

    run(scenario())


def test_missing_or_mismatched_tenant_context_fails_closed(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, factory, tenant_a, tenant_b = await database(tmp_path)
        try:
            with pytest.raises(ValueError, match="tenant context"):
                factory()
            async with tenant_transaction(factory, tenant_a) as db:
                with pytest.raises(ValueError, match="tenant context mismatch"):
                    await create_repository(db, tenant_b, name="substitution")
        finally:
            await engine.dispose()

    run(scenario())


def test_duplicate_event_is_idempotent_and_outbox_is_atomic(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, factory, tenant_a, _tenant_b = await database(tmp_path)
        try:
            host_id, repo_id, session_id = await topology(factory, tenant_a)
            async with tenant_transaction(factory, tenant_a) as db:
                first = await ingest_event(
                    db,
                    tenant_a,
                    host_id=host_id,
                    repository_id=repo_id,
                    session_id=session_id,
                    event_id="evt_1",
                    kind="tool_call",
                    payload={"safe": True},
                    local_log_seq=8,
                    repo_seq=5,
                )
                first_sequences = (first.cloud_ingest_seq, first.session_seq)
            async with tenant_transaction(factory, tenant_a) as db:
                second = await ingest_event(
                    db,
                    tenant_a,
                    host_id=host_id,
                    repository_id=repo_id,
                    session_id=session_id,
                    event_id="evt_1",
                    kind="tool_call",
                    payload={"safe": True},
                    local_log_seq=8,
                    repo_seq=5,
                )
                assert (second.cloud_ingest_seq, second.session_seq) == first_sequences
                assert await db.scalar(select(func.count(Event.id))) == 1
                assert await db.scalar(select(func.count(RelayOutbox.id))) == 1
                with pytest.raises(ValueError, match="conflicting provenance"):
                    await ingest_event(
                        db,
                        tenant_a,
                        host_id=host_id,
                        repository_id=repo_id,
                        session_id=session_id,
                        event_id="evt_1",
                        kind="tool_call",
                        payload={"safe": False},
                        local_log_seq=8,
                        repo_seq=5,
                    )
        finally:
            await engine.dispose()

    run(scenario())


def test_sequence_domains_are_never_conflated(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, factory, tenant_a, _tenant_b = await database(tmp_path)
        try:
            host_id, repo_id, session_id = await topology(factory, tenant_a)
            async with tenant_transaction(factory, tenant_a) as db:
                event = await ingest_event(
                    db,
                    tenant_a,
                    host_id=host_id,
                    repository_id=repo_id,
                    session_id=session_id,
                    event_id="evt_domains",
                    kind="change",
                    payload={},
                    local_log_seq=900,
                    repo_seq=700,
                )
                assert event.local_log_seq == 900
                assert event.repo_seq == 700
                assert event.session_seq == 1
                assert event.cloud_ingest_seq >= 1
                assert event.client_stream_seq is None
            host_b, repo_b, session_b = await topology(factory, _tenant_b)
            async with tenant_transaction(factory, _tenant_b) as db:
                other = await ingest_event(
                    db,
                    _tenant_b,
                    host_id=host_b,
                    repository_id=repo_b,
                    session_id=session_b,
                    event_id="evt_other_tenant",
                    kind="change",
                    payload={},
                    local_log_seq=1,
                    repo_seq=1,
                )
                assert other.cloud_ingest_seq > event.cloud_ingest_seq
        finally:
            await engine.dispose()

    run(scenario())


def test_initial_migration_enables_and_forces_rls_for_tenant_tables() -> None:
    migration = (
        Path(__file__).resolve().parents[1] / "alembic/versions/0001_initial.py"
    ).read_text()

    for table in Base.metadata.tables.values():
        if "tenant_id" not in table.columns:
            continue
        assert repr(table.name) in migration
    assert "ENABLE ROW LEVEL SECURITY" in migration
    assert "FORCE ROW LEVEL SECURITY" in migration
    assert "CREATE POLICY tenant_isolation" in migration
    assert "current_setting('app.tenant_id', true)" in migration


async def database(tmp_path: Path):
    engine = create_database_engine(f"sqlite+aiosqlite:///{tmp_path / 'control.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()
        await connection.execute(
            Tenant.__table__.insert(),
            [
                {"id": tenant_a, "name": "tenant-a"},
                {"id": tenant_b, "name": "tenant-b"},
            ],
        )
    return (
        engine,
        tenant_session_factory(engine),
        TenantContext(tenant_id=tenant_a),
        TenantContext(tenant_id=tenant_b),
    )


async def topology(factory, context: TenantContext):
    async with tenant_transaction(factory, context) as db:
        host = Host(tenant_id=context.tenant_id, name="host", public_key="ed25519:test")
        db.add(host)
        await db.flush()
        repository = await create_repository(
            db,
            context,
            name="repo",
            host_id=host.id,
            canonical_identity="sha256:" + "a" * 64,
        )
        session = Session(
            tenant_id=context.tenant_id,
            repository_id=repository.id,
            host_id=host.id,
            local_session_id="local-1",
            state_hash="sha256:" + "b" * 64,
        )
        db.add(session)
        await db.flush()
        return host.id, repository.id, session.id
