"""coddpiece — A Python relational algebra teaching library.

Implements Codd's relational algebra backed by a real database
via DB-API 2.0. Teaches relational algebra so SQL makes sense.

Quick start:
    import sqlite3
    from coddpiece import Engine, count, sum_, avg

    engine = Engine(sqlite3.connect(":memory:"))

    from coddpiece.datasets import suppliers_and_parts
    s, p, sp = suppliers_and_parts(engine)

    # Names of London suppliers
    s.select(s.city == "London").project("sname").collect()
"""

# Public API surface. Internal modules (compiler, display, predicates) are
# intentionally excluded — users interact with them indirectly through
# BaseRelation methods like .explain(), .select(), etc.
from .aggregates import avg, count, max_, min_, sum_
from .datasets import employees_db, suppliers_and_parts
from .engine import Engine
from .errors import (
    AttributeError_,
    DomainError,
    EngineError,
    PredicateError,
    RelationalError,
    SchemaError,
)
from .relation import Relation
from .schema import Attribute, Schema

__all__ = [
    "Engine",
    "Schema",
    "Attribute",
    "Relation",
    "count",
    "sum_",
    "avg",
    "min_",
    "max_",
    "suppliers_and_parts",
    "employees_db",
    "RelationalError",
    "SchemaError",
    "DomainError",
    "AttributeError_",
    "EngineError",
    "PredicateError",
]
