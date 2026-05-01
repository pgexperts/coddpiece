"""Database engine and dialect handling for DB-API 2.0 connections.

The Engine wraps any PEP 249 connection. The Dialect adapts to
the connection's parameter style, quoting, and type system.

Role in the system: this file is the bottom of the stack. It translates
compiled SQL from compiler.py into actual DB-API execute() calls. To
support a new backend, extend Dialect's detection branches (module-name
sniffing) and, if needed, override placeholder(), quote_identifier(),
and _introspect_schema() below.

Invariant: all SQL issued from here must use parameterized queries —
literal values must never be interpolated into SQL text. The Dialect's
placeholder() / format_params() pair is the one-and-only mechanism.
"""

from __future__ import annotations

import importlib
from contextlib import closing
from typing import Any

from .compiler import Compiler
from .relation import BaseRelation, Relation
from .schema import SQL_TO_PYTHON, SUPPORTED_DOMAINS, Attribute, Schema


class Dialect:
    """Adapter for DB-API 2.0 connection quirks.

    Sniffs the paramstyle from the connection's module and adapts
    placeholder generation and identifier quoting accordingly.

    Extension point for a new backend: paramstyle is discovered
    generically via the driver module's PEP 249 `paramstyle` attribute,
    so most drivers Just Work. Identifier quoting, however, is
    hard-coded per driver (see _detect_quote_char) and must be
    extended when adding a backend whose quoting differs from ANSI
    double-quotes.
    """

    def __init__(self, connection: Any):
        self._connection = connection
        self.paramstyle = self._detect_paramstyle(connection)
        self.quote_char = self._detect_quote_char(connection)
        # Which set operators support the `... ALL` (bag/multiset) form.
        # UNION ALL is universally supported by SQL backends. INTERSECT ALL
        # and EXCEPT ALL are part of the SQL standard but SQLite has never
        # implemented them. PostgreSQL and MySQL 8+ do. Reporting this per
        # dialect lets BagWrapper raise a clear error on backends that
        # cannot honor bag semantics for INTERSECT/EXCEPT, instead of
        # confusing users with a driver-level "syntax error near ALL".
        self.setop_all_support = self._detect_setop_all_support(connection)

    def _detect_setop_all_support(self, connection: Any) -> frozenset[str]:
        """Return the set of set ops for which `<OP> ALL` is supported."""
        module_name = type(connection).__module__
        # SQLite: only UNION ALL.
        if "sqlite" in module_name:
            return frozenset({"UNION"})
        # Everywhere else (PG, MySQL 8+, MSSQL, ...): assume full support.
        # If a future backend disagrees, add a branch here rather than
        # hiding the limitation behind a runtime SQL error.
        return frozenset({"UNION", "INTERSECT", "EXCEPT"})

    def _detect_paramstyle(self, connection: Any) -> str:
        """Detect the parameter style from the connection's module."""
        # Walk up the module hierarchy (e.g., psycopg2.extensions → psycopg2)
        # because paramstyle is typically on the top-level module, but the
        # connection class may live in a submodule. Falls back to qmark (SQLite).
        #
        # Only ImportError is swallowed: a missing intermediate package is
        # expected during the walk. Anything else (a driver whose import
        # raises a real error, or a module whose __getattr__ misbehaves)
        # propagates so the user gets a meaningful failure instead of
        # silently defaulting to qmark and emitting unbindable SQL on
        # PostgreSQL/MySQL drivers.
        module_name = type(connection).__module__
        parts = module_name.split(".")
        for i in range(len(parts), 0, -1):
            try:
                mod = importlib.import_module(".".join(parts[:i]))
            except ImportError:
                continue
            if hasattr(mod, "paramstyle"):
                return mod.paramstyle
        # Default to qmark (SQLite style)
        return "qmark"

    def _detect_quote_char(self, connection: Any) -> str:
        """Detect the identifier quote character."""
        # Module-name sniffing is deliberate: PEP 249 offers no standard
        # API for identifier quoting, so we match on driver package name.
        # New backends that do not use ANSI double-quotes must add a branch.
        module_name = type(connection).__module__
        # PostgreSQL uses double quotes
        if "psycopg" in module_name or "pg8000" in module_name:
            return '"'
        # MySQL uses backticks
        if "mysql" in module_name or "pymysql" in module_name:
            return "`"
        # Default: double quotes (SQL standard, works for SQLite)
        return '"'

    def placeholder(self, index: int = 0) -> str:
        """Return the appropriate placeholder for a parameter.

        This is the single choke point enforcing the "no literal
        interpolation" invariant: the compiler asks here for the token
        to splice into SQL, and the real value is passed separately
        via the driver's execute() parameter list.
        """
        # index matters for numeric/named/pyformat (each placeholder is distinct);
        # qmark and format ignore it since all placeholders are identical.
        match self.paramstyle:
            case "qmark":
                return "?"
            case "numeric":
                return f":{index + 1}"
            case "named":
                return f":p{index}"
            case "format":
                return "%s"
            case "pyformat":
                return f"%(p{index})s"
            case _:
                return "?"

    def quote_identifier(self, name: str) -> str:
        """Quote a table or column identifier.

        Identifiers cannot use parameterized placeholders (SQL forbids
        it), so they are quoted here instead. This is safe because the
        quote char is doubled when escaping, preventing identifier
        injection even for adversarial names.
        """
        q = self.quote_char
        # SQL standard escaping: double the quote char inside the identifier
        # (e.g., "foo""bar" for a column literally named foo"bar).
        escaped = name.replace(q, q + q)
        return f"{q}{escaped}{q}"

    def format_params(self, params: list) -> Any:
        """Format params for the connection's paramstyle."""
        # named/pyformat styles require a dict keyed by placeholder name (p0, p1, ...),
        # matching the keys generated by placeholder(). All other styles use a list.
        if self.paramstyle == "named":
            return {f"p{i}": v for i, v in enumerate(params)}
        if self.paramstyle == "pyformat":
            return {f"p{i}": v for i, v in enumerate(params)}
        return params


class Engine:
    """Manages a DB-API 2.0 connection and executes compiled expressions.

    This is the entry point for creating and querying relations.

    Connection lifetime: the caller owns the connection. Engine does
    not open or close it, and assumes it remains usable for the life
    of the Engine. Transactions: _create_table and _insert_rows commit
    explicitly; execute() does not, so read queries participate in
    whatever transaction the caller has open.
    """

    def __init__(self, connection: Any):
        self.connection = connection
        self.dialect = Dialect(connection)

    def relation(self, table_name: str) -> Relation:
        """Wrap an existing database table as a Relation.

        Introspects the table to determine its schema.
        """
        schema = self._introspect_schema(table_name)
        return Relation(self, table_name, schema)

    def create(
        self,
        name: str,
        attrs: dict[str, type],
        rows: list[tuple] | None = None,
    ) -> Relation:
        """Create a new table, optionally populating it with data.

        Args:
            name: Table name
            attrs: Ordered dict of {attr_name: python_type}
            rows: Optional list of tuples to insert
        """
        schema = Schema(tuple(Attribute(n, t) for n, t in attrs.items()))
        self._create_table(name, schema)
        if rows:
            self._insert_rows(name, schema, rows)
        return self.relation(name)

    def execute(self, expr: BaseRelation) -> list[tuple[Any, ...]]:
        """Compile an expression to SQL and execute it."""
        # Always re-compile: expression trees are immutable but users may
        # mutate the database between calls, so caching would not help.
        # A fresh Compiler is also the simplest way to get fresh parameter
        # indices for each execution.
        compiler = Compiler(self.dialect)
        sql, params = compiler.compile(expr)
        formatted_params = self.dialect.format_params(params)
        # contextlib.closing ensures the cursor is released even if the
        # driver raises during execute or fetchall. PEP 249 requires every
        # cursor to expose .close(); not every driver makes cursors usable
        # as native context managers (sqlite3 doesn't), so we route through
        # closing() for portability rather than using `with cursor as ...`.
        with closing(self.connection.cursor()) as cursor:
            cursor.execute(sql, formatted_params)
            return cursor.fetchall()

    # --- Internal helpers ---

    def _introspect_schema(self, table_name: str) -> Schema:
        """Determine a table's schema from the database.

        Each backend exposes column metadata differently, so this is
        the third method (after placeholder and quote_identifier) that
        a new backend typically needs to extend. Preferred order: a
        backend-native catalog query that preserves declared types,
        then the cursor.description fallback that loses them.
        """
        # Three-tier fallback: SQLite PRAGMA → PostgreSQL information_schema
        # → generic cursor.description. The generic path loses type info
        # (defaults to str) because cursor.description type_code is backend-specific.
        # All three branches share `with closing(...)` so the cursor is
        # released even if the introspection raises (matters for PG/MySQL
        # under load; SQLite in-memory does not care).
        module_name = type(self.connection).__module__

        # SQLite: use PRAGMA
        # PRAGMA arguments cannot be parameterized via the driver, so the
        # table name is interpolated. Two layers of safety:
        #   1. isidentifier() rejects anything that wouldn't be a legal
        #      Python identifier, matching the same invariant Attribute
        #      already enforces on column names. This keeps adversarial or
        #      structurally-odd inputs (embedded quotes, semicolons, dots)
        #      out of the SQL text entirely.
        #   2. PRAGMA also accepts a quoted-identifier form, so we route
        #      the validated name through the dialect's quote_identifier
        #      to handle reserved words like "order" or "select" that
        #      pass isidentifier() but are not safe unquoted.
        # `Engine.relation()` is a public entry point — it cannot assume
        # callers are trusted, so this gate must hold even for wrapping
        # an existing table.
        if "sqlite" in module_name:
            if not isinstance(table_name, str) or not table_name.isidentifier():
                raise ValueError(
                    f"Invalid table name {table_name!r}: "
                    f"must be a valid Python identifier."
                )
            qname = self.dialect.quote_identifier(table_name)
            with closing(self.connection.cursor()) as cursor:
                cursor.execute(f"PRAGMA table_info({qname})")
                rows = cursor.fetchall()
            if not rows:
                raise ValueError(f"Table {table_name!r} not found.")
            attrs = []
            for row in rows:
                col_name = row[1]
                col_type = row[2].upper() if row[2] else "TEXT"
                # Map SQL type to Python type
                py_type = self._sql_type_to_python(col_type)
                attrs.append(Attribute(col_name, py_type))
            return Schema(tuple(attrs))

        # PostgreSQL (psycopg2, psycopg3, pg8000): use information_schema.
        #
        # CAVEAT: this is the one place outside Dialect where a paramstyle
        # is hardcoded. The earlier comment claimed every supported PG
        # driver uses 'format' paramstyle — that was wrong. psycopg3 (and
        # psycopg2) report 'pyformat' (`%(name)s`); only pg8000 uses
        # 'format' (`%s`). The bare %s below works because psycopg accepts
        # both shapes at execute time as a forgiveness behavior, and pg8000
        # native-supports it. We rely on that tolerance rather than route
        # through self.dialect.placeholder() / format_params() for one
        # query. If a future PG driver appears that strictly enforces its
        # declared paramstyle (refusing %s under pyformat), rewrite this
        # branch to use the Dialect like the rest of the compiler does.
        # Treat this as a known minor inconsistency, not a precedent.
        if any(x in module_name for x in ("psycopg", "pg8000", "postgresql")):
            with closing(self.connection.cursor()) as cursor:
                cursor.execute(
                    "SELECT column_name, data_type "
                    "FROM information_schema.columns "
                    "WHERE table_name = %s ORDER BY ordinal_position",
                    (table_name,),
                )
                rows = cursor.fetchall()
            if not rows:
                raise ValueError(f"Table {table_name!r} not found.")
            attrs = []
            for col_name, data_type in rows:
                py_type = self._sql_type_to_python(data_type.upper())
                attrs.append(Attribute(col_name, py_type))
            return Schema(tuple(attrs))

        # Fallback: SELECT * LIMIT 0 + cursor.description.
        #
        # PEP 249 guarantees that every conforming driver exposes
        # connection.Error as the root of its exception hierarchy. We
        # narrow the catch to that family so an unrelated bug elsewhere
        # in the call stack still propagates with its original traceback.
        # Translation to ValueError keeps the user-facing API uniform with
        # the SQLite and Postgres branches above (they raise ValueError on
        # "table not found").
        q = self.dialect.quote_identifier(table_name)
        with closing(self.connection.cursor()) as cursor:
            try:
                cursor.execute(f"SELECT * FROM {q} LIMIT 0")
            except getattr(self.connection, "Error", Exception) as exc:
                raise ValueError(f"Table {table_name!r} not found.") from exc
            # PEP 249 says cursor.description is None only for statements
            # that produced no result set (DDL, etc.); a successful SELECT
            # always populates it. The check is defensive, not load-bearing.
            description = cursor.description or ()
        attrs = []
        for desc in description:
            col_name = desc[0]
            # cursor.description type_code is backend-specific; default to str
            attrs.append(Attribute(col_name, str))
        return Schema(tuple(attrs))

    def _sql_type_to_python(self, sql_type: str) -> type:
        """Map a SQL type string to a Python type."""
        # Exact match first, then bounded prefix match for parameterized types
        # like VARCHAR(255). The prefix must end at a non-identifier boundary
        # so spurious matches like INTERVAL → "INT" → int don't slip through.
        # `INT(11)` (MySQL) and `INTEGER` (SQLite/PG) still match correctly
        # because the boundary character ('(' or end-of-string) is not part of
        # an identifier. Falls back to str for unknown types — lenient by
        # design to maximize backend compatibility.
        sql_type = sql_type.upper().strip()
        if sql_type in SQL_TO_PYTHON:
            return SQL_TO_PYTHON[sql_type]
        for sql_key, py_type in SQL_TO_PYTHON.items():
            if sql_type.startswith(sql_key):
                rest = sql_type[len(sql_key):]
                # Identifier-character continuation means this is a different
                # type that happens to share a prefix — reject the match.
                if rest and (rest[0].isalnum() or rest[0] == "_"):
                    continue
                return py_type
        return str

    def _create_table(self, name: str, schema: Schema) -> None:
        """Issue CREATE TABLE."""
        # DDL cannot be parameterized — both table name and column types
        # must be inlined. Safety comes from quote_identifier for names
        # and from the SUPPORTED_DOMAINS lookup table for types (no
        # free-form strings reach the SQL).
        cols = []
        for attr in schema.attributes:
            col_name = self.dialect.quote_identifier(attr.name)
            sql_type = SUPPORTED_DOMAINS.get(attr.domain, "TEXT")
            cols.append(f"{col_name} {sql_type}")
        col_defs = ", ".join(cols)
        table = self.dialect.quote_identifier(name)
        with closing(self.connection.cursor()) as cursor:
            cursor.execute(f"CREATE TABLE {table} ({col_defs})")
        self.connection.commit()

    def _insert_rows(self, name: str, schema: Schema, rows: list[tuple]) -> None:
        """Insert rows into a table."""
        # Row values are always parameterized (upholding the no-literal
        # invariant). Rows are executed one at a time rather than via
        # executemany() to keep error messages referencing individual
        # bad rows — this is a teaching library, so clarity beats bulk
        # throughput.
        #
        # Pedagogical aside for readers studying this code: in real
        # application code, prefer cursor.executemany() for inserting many
        # rows. It is dramatically faster (one round-trip per batch on PG
        # or MySQL instead of one per row) and most drivers still surface
        # per-row constraint errors. The per-row pattern here is chosen for
        # readability, not as a recommended template.
        if not rows:
            return
        table = self.dialect.quote_identifier(name)
        n_cols = len(schema)
        placeholders = ", ".join(self.dialect.placeholder(i) for i in range(n_cols))
        with closing(self.connection.cursor()) as cursor:
            # format_params on each row is necessary because named paramstyles
            # need a dict, not a list. Commits once after all rows, not per row.
            for row in rows:
                formatted = self.dialect.format_params(list(row))
                cursor.execute(
                    f"INSERT INTO {table} VALUES ({placeholders})", formatted
                )
        self.connection.commit()
