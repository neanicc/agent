#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os

import psycopg


BASELINE_TABLES = {
    "tenants",
    "users",
    "memberships",
    "repositories",
    "repairs",
    "repair_candidates",
}
CURRENT_TABLES = BASELINE_TABLES | {
    "meter_entries",
    "quota_reservations",
    "billing_subscriptions",
    "billing_events",
    "billing_adjustments",
    "billing_invoices",
    "price_catalogs",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe LoopGuard migration compatibility")
    parser.add_argument("--contract", choices=("baseline", "current"), required=True)
    args = parser.parse_args()
    url = os.environ.get("LOOPGUARD_API_DATABASE_URL", "").strip()
    if not url.startswith(("postgresql://", "postgres://")):
        raise SystemExit("LOOPGUARD_API_DATABASE_URL must be a PostgreSQL URL")
    required = BASELINE_TABLES if args.contract == "baseline" else CURRENT_TABLES
    with psycopg.connect(url, connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                """
            )
            present = {str(row[0]) for row in cursor.fetchall()}
            missing = sorted(required - present)
            if missing:
                raise SystemExit("migration contract is missing tables: " + ", ".join(missing))
            cursor.execute("SELECT version_num FROM alembic_version")
            revision = cursor.fetchone()
            if revision is None or not str(revision[0]).strip():
                raise SystemExit("migration contract has no Alembic revision")
            cursor.execute("SELECT 1")
            if cursor.fetchone() != (1,):
                raise SystemExit("migration database is not queryable")


if __name__ == "__main__":
    main()
