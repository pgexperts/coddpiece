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
from typing import Any

from .compiler import Compiler
from .schema import SUPPORTED_DOMAINS, SQL_TO_PYTHON, Attribute, Schema
from .relation import BaseRelation, Relation


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

    def _detect_paramstyle(self, connection: Any) -> str:
        """Detect the parameter style from the connection's module."""
        # Walk up the module hierarchy (e.g., psycopg2.extensions → psycopg2)
        # because paramstyle is typically on the top-level module, but the
        # connection class may live in a submodule. Falls back to qmark (SQLite).
        module_name = type(connection).__module__
        try:
            parts = module_name.split(".")
            for i in range(len(parts), 0, -1):
                try:
                    mod = importlib.import_module(".".join(parts[:i]))
                    if hasattr(mod, "paramstyle"):
                        return mod.paramstyle
                except ImportError:
                    continue
        except Exception:
            pass
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

    def execute(self, expr: BaseRelation) -> list[tuple]:
        """Compile an expression to SQL and execute it."""
        # Always re-compile: expression trees are immutable but users may
        # mutate the database between calls, so caching would not help.
        # A fresh Compiler is also the simplest way to get fresh parameter
        # indices for each execution.
        compiler = Compiler(self.dialect)
        sql, params = compiler.compile(expr)
        formatted_params = self.dialect.format_params(params)
        cursor = self.connection.cursor()
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
        cursor = self.connection.cursor()
        module_name = type(self.connection).__module__

        # SQLite: use PRAGMA
        # PRAGMA does not accept parameterized arguments, hence the
        # f-string. This is the only place in the module where a table
        # identifier is interpolated directly; callers are trusted here
        # (introspection is driven by code-supplied names, not user input).
        if "sqlite" in module_name:
            cursor.execute(f"PRAGMA table_info({table_name})")
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

        # PostgreSQL (psycopg2, psycopg3, pg8000): use information_schema
        # Hard-coded %s placeholder: every supported PG driver uses
        # 'format' paramstyle, so we bypass the Dialect here. If a PG
        # driver ever appears that uses a different paramstyle, this
        # branch must be rewritten to go through self.dialect.placeholder().
        if any(x in module_name for x in ("psycopg", "pg8000", "postgresql")):
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

        # Fallback: SELECT * LIMIT 0 + cursor.description
        q = self.dialect.quote_identifier(table_name)
        cursor.execute(f"SELECT * FROM {q} LIMIT 0")
        if cursor.description is None:
            raise ValueError(f"Table {table_name!r} not found or has no columns.")
        attrs = []
        for desc in cursor.description:
            col_name = desc[0]
            # cursor.description type_code is backend-specific; default to str
            attrs.append(Attribute(col_name, str))
        return Schema(tuple(attrs))

    def _sql_type_to_python(self, sql_type: str) -> type:
        """Map a SQL type string to a Python type."""
        # Exact match first, then prefix match for parameterized types like
        # VARCHAR(255). Falls back to str for unknown types — lenient by design
        # to maximize backend compatibility.
        sql_type = sql_type.upper().strip()
        if sql_type in SQL_TO_PYTHON:
            return SQL_TO_PYTHON[sql_type]
        for sql_key, py_type in SQL_TO_PYTHON.items():
            if sql_type.startswith(sql_key):
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
        cursor = self.connection.cursor()
        cursor.execute(f"CREATE TABLE {table} ({col_defs})")
        self.connection.commit()

    def _insert_rows(self, name: str, schema: Schema, rows: list[tuple]) -> None:
        """Insert rows into a table."""
        # Row values are always parameterized (upholding the no-literal
        # invariant). Rows are executed one at a time rather than via
        # executemany() to keep error messages referencing individual
        # bad rows — this is a teaching library, so clarity beats bulk
        # throughput.
        if not rows:
            return
        table = self.dialect.quote_identifier(name)
        n_cols = len(schema)
        placeholders = ", ".join(self.dialect.placeholder(i) for i in range(n_cols))
        cursor = self.connection.cursor()
        # format_params on each row is necessary because named paramstyles need
        # a dict, not a list. Commits once after all rows, not per row.
        for row in rows:
            formatted = self.dialect.format_params(list(row))
            cursor.execute(f"INSERT INTO {table} VALUES ({placeholders})", formatted)
        self.connection.commit()
