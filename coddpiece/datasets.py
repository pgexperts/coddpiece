"""Built-in example datasets for relational algebra exercises.

These are the classic datasets used in relational algebra teaching:
- Suppliers and Parts (C.J. Date)
- Employees and Departments (introductory)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import Engine
    from .relation import Relation


# The suppliers-and-parts dataset is from C.J. Date's "An Introduction to
# Database Systems" — the standard reference dataset in relational database
# education since the 1970s. Data values (S1-S5, P1-P6) are canonical.

def suppliers_and_parts(engine: Engine) -> tuple[Relation, Relation, Relation]:
    """Create the classic Date suppliers-and-parts dataset.

    Returns:
        (s, p, sp) — Suppliers, Parts, and Shipments relations.
    """
    s = engine.create(
        "s",
        {"sno": str, "sname": str, "status": int, "city": str},
        rows=[
            ("S1", "Smith", 20, "London"),
            ("S2", "Jones", 10, "Paris"),
            ("S3", "Blake", 30, "Paris"),
            ("S4", "Clark", 20, "London"),
            ("S5", "Adams", 30, "Athens"),
        ],
    )

    p = engine.create(
        "p",
        {"pno": str, "pname": str, "color": str, "weight": float, "city": str},
        rows=[
            ("P1", "Nut", "Red", 12.0, "London"),
            ("P2", "Bolt", "Green", 17.0, "Paris"),
            ("P3", "Screw", "Blue", 17.0, "Oslo"),
            ("P4", "Screw", "Red", 14.0, "London"),
            ("P5", "Cam", "Blue", 12.0, "Paris"),
            ("P6", "Cog", "Red", 19.0, "London"),
        ],
    )

    sp = engine.create(
        "sp",
        {"sno": str, "pno": str, "qty": int},
        rows=[
            ("S1", "P1", 300),
            ("S1", "P2", 200),
            ("S1", "P3", 400),
            ("S1", "P4", 200),
            ("S1", "P5", 100),
            ("S1", "P6", 100),
            ("S2", "P1", 300),
            ("S2", "P2", 400),
            ("S3", "P2", 200),
            ("S4", "P2", 200),
            ("S4", "P4", 300),
            ("S4", "P5", 400),
        ],
    )

    return s, p, sp


# Simpler dataset for introductory exercises. Uses a string foreign key
# (department→departments.name) instead of a numeric ID so that natural
# joins work out of the box without needing to understand surrogate keys.

def employees_db(engine: Engine) -> tuple[Relation, Relation]:
    """Create a simple employees/departments dataset.

    Returns:
        (employees, departments) relations.
    """
    departments = engine.create(
        "departments",
        {"name": str, "budget": int, "location": str},
        rows=[
            ("Engineering", 500000, "San Francisco"),
            ("Sales", 300000, "New York"),
            ("HR", 200000, "Chicago"),
            ("Marketing", 250000, "New York"),
        ],
    )

    employees = engine.create(
        "employees",
        {"eid": int, "ename": str, "department": str, "salary": int},
        rows=[
            (1, "Alice", "Engineering", 120000),
            (2, "Bob", "Engineering", 110000),
            (3, "Carol", "Sales", 90000),
            (4, "Dave", "Sales", 85000),
            (5, "Eve", "HR", 95000),
            (6, "Frank", "Marketing", 88000),
            (7, "Grace", "Engineering", 130000),
            (8, "Heidi", "HR", 92000),
        ],
    )

    return employees, departments
