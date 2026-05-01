"""Shared test fixtures."""

import contextlib
import os
import sqlite3
import uuid

import pytest

from coddpiece import Engine
from coddpiece.datasets import employees_db, suppliers_and_parts


@pytest.fixture
def engine():
    conn = sqlite3.connect(":memory:")
    return Engine(conn)


@pytest.fixture
def sp_data(engine):
    """Suppliers-and-parts dataset. Returns (s, p, sp, engine)."""
    s, p, sp = suppliers_and_parts(engine)
    return s, p, sp, engine


@pytest.fixture
def emp_data(engine):
    """Employees dataset. Returns (employees, departments, engine)."""
    employees, departments = employees_db(engine)
    return employees, departments, engine


@pytest.fixture
def pg_engine():
    """PostgreSQL engine, skipped unless DATABASE_URL is set.

    Each test runs in its own freshly-created PG schema, dropped at
    teardown. Per-test isolation matters for two reasons:
      1. tables created inside the test (Engine.create) are namespaced
         to the schema, so concurrent test runs against the same DB do
         not collide on table names; and
      2. teardown's DROP SCHEMA ... CASCADE cleans up everything the
         test created, including any objects we forgot about, so the
         next test starts on a clean slate without per-table cleanup
         hooks.

    DATABASE_URL is the standard psycopg connection-URL format,
    e.g. postgresql://user:pass@host:port/dbname. CI sets it; local
    runs without it skip these tests rather than fail.
    """
    pg_url = os.environ.get("DATABASE_URL")
    if not pg_url:
        pytest.skip("DATABASE_URL not set; PostgreSQL tests skipped.")
    psycopg = pytest.importorskip("psycopg")

    conn = psycopg.connect(pg_url)
    # uuid4 hex prefix keeps the name short, identifier-shaped, and
    # collision-free across parallel runners.
    schema_name = f"coddpiece_test_{uuid.uuid4().hex[:12]}"
    with conn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA "{schema_name}"')
        cur.execute(f'SET search_path TO "{schema_name}"')
    conn.commit()
    try:
        yield Engine(conn)
    finally:
        # Best-effort cleanup. If a test failed mid-transaction, the PG
        # connection may be in an aborted state and any new statement
        # would re-raise the original error; rollback first so the
        # DROP SCHEMA can actually execute. The suppress is broad on
        # purpose — if rollback itself raises, we still want to attempt
        # the schema drop and the connection close.
        with contextlib.suppress(Exception):
            conn.rollback()
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA "{schema_name}" CASCADE')
        conn.commit()
        conn.close()
