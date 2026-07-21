from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer

from loopguard_api.db import (
    TenantContext,
    create_repository,
    tenant_session_factory,
    tenant_transaction,
)


@pytest.mark.skipif(
    os.environ.get("LOOPGUARD_RUN_TESTCONTAINERS") != "1",
    reason="set LOOPGUARD_RUN_TESTCONTAINERS=1 on a Docker-enabled CI worker",
)
def test_postgres_rls_blocks_even_raw_cross_tenant_queries(monkeypatch) -> None:
    import asyncio

    root = Path(__file__).resolve().parents[1]
    with PostgresContainer("postgres:16-alpine", driver="psycopg") as postgres:
        admin_url = postgres.get_connection_url(driver="asyncpg")
        monkeypatch.setenv("LOOPGUARD_API_DATABASE_URL", admin_url)
        alembic = Config(root / "alembic.ini")
        command.upgrade(alembic, "head")

        async def scenario() -> None:
            admin = create_async_engine(admin_url)
            tenant_a = uuid.uuid4()
            tenant_b = uuid.uuid4()
            password = "rls-test-password"
            try:
                async with admin.begin() as connection:
                    await connection.execute(
                        text("INSERT INTO tenants (id, name) VALUES (:a, 'a'), (:b, 'b')"),
                        {"a": tenant_a, "b": tenant_b},
                    )
                    await connection.execute(
                        text(f"CREATE ROLE loopguard_app LOGIN PASSWORD '{password}' NOSUPERUSER")
                    )
                    await connection.execute(text("GRANT USAGE ON SCHEMA public TO loopguard_app"))
                    await connection.execute(
                        text(
                            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                            "IN SCHEMA public TO loopguard_app"
                        )
                    )
                    await connection.execute(
                        text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO loopguard_app")
                    )
            finally:
                await admin.dispose()

            app_url = make_url(admin_url).set(
                username="loopguard_app", password=password
            ).render_as_string(hide_password=False)
            application = create_async_engine(app_url)
            factory = tenant_session_factory(application)
            try:
                for tenant_id, name in ((tenant_a, "private-a"), (tenant_b, "private-b")):
                    context = TenantContext(tenant_id=tenant_id)
                    async with tenant_transaction(factory, context) as database:
                        await create_repository(database, context, name=name)
                        assert await database.scalar(text("SELECT count(*) FROM repositories")) == 1
                async with application.connect() as connection:
                    assert await connection.scalar(text("SELECT count(*) FROM repositories")) == 0
            finally:
                await application.dispose()

        asyncio.run(scenario())
        command.downgrade(alembic, "base")
