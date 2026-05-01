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
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

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

# Single source of truth for the version is pyproject.toml; we read the
# installed metadata at import time. The PackageNotFoundError fallback
# covers the editable-install / source-checkout case where metadata may
# not be present (e.g. running from an unbuilt source tree).
try:
    __version__ = _pkg_version("coddpiece")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0+unknown"

__all__ = [
    "__version__",
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
