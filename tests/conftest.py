"""Shared test fixtures."""

import sqlite3
import pytest
from coddpiece import Engine
from coddpiece.datasets import suppliers_and_parts, employees_db


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
