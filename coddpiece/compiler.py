"""SQL compiler for relational algebra expressions.

Walks the expression tree and generates parameterized SQL.
Optimizes for readability: base relations are referenced directly
as table names rather than wrapped in unnecessary subqueries.

Role in the system: this is the visitor layer between the algebra tree
(relation.py) and the DB adapter (engine.py). The teaching goal is
clean, idiomatic SQL output that a student can read and recognize —
not the smallest or fastest SQL, but SQL that mirrors the algebra.

Hard invariants enforced here:
  * All literal values flow through self.params; they are NEVER
    string-interpolated into the SQL text. This is both a security
    rule (no injection) and a teaching rule (students should see
    that literals become bind parameters).
  * Each expression node is visited exactly once per compile, with
    one documented exception: Division, which needs the dividend
    subtree twice to build its correlated subquery.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .predicates import Attr, CompoundPredicate, Literal, NotPredicate, Predicate
from .relation import (
    Antijoin,
    BaseRelation,
    CrossProduct,
    Difference,
    Division,
    Equijoin,
    Grouping,
    Intersect,
    NaturalJoin,
    OuterJoin,
    Projection,
    Relation,
    Rename,
    Selection,
    Semijoin,
    ThetaJoin,
    Union,
    _SetOp,
)

if TYPE_CHECKING:
    from .engine import Dialect


class Compiler:
    """Compiles a relational algebra expression tree to SQL.

    The compiler generates clean, readable SQL suitable for teaching.
    Base relations appear as table names (not subqueries), and simple
    chains like select→project are flattened into single queries.
    """

    def __init__(self, dialect: Dialect, bag_mode: bool = False):
        self.dialect = dialect
        # Flat list accumulating all query parameters across the entire tree.
        # Positional placeholders in the SQL correspond 1:1 to entries here.
        # Element type is `Any` because the algebra accepts any Python value
        # the user's domain registry permits (int/str/Decimal/datetime/...).
        self.params: list[Any] = []
        # Monotonic counter for generating unique subquery aliases (t1, t2, ...).
        self._alias_counter = 0
        # When True, emit SQL with bag (multiset) semantics throughout: no
        # DISTINCT on individual SELECTs, and UNION ALL / INTERSECT ALL /
        # EXCEPT ALL on set operators. Used by BagWrapper. Threading this
        # as a flag (rather than post-hoc string replacement) is what makes
        # `.bags()` actually deliver bag semantics over set operations,
        # which the textbook teaching point requires.
        self.bag_mode = bag_mode

    def _select(self) -> str:
        """SELECT keyword honoring bag_mode (with DISTINCT or without)."""
        return "SELECT" if self.bag_mode else "SELECT DISTINCT"

    def _setop_keyword(self, op_name: str) -> str:
        """Set-op keyword honoring bag_mode (UNION/INTERSECT/EXCEPT [ALL])."""
        # Bag-mode set ops use ALL: SQL's bare UNION/INTERSECT/EXCEPT are
        # set operators (they dedupe). Without this branch, .bags() over
        # a set operator would silently dedupe the result and the
        # set-vs-bag teaching contrast would be invisible.
        if not self.bag_mode:
            return op_name
        # Dialect-aware: SQLite has never implemented INTERSECT ALL or
        # EXCEPT ALL. Surface the limitation here as a clear error rather
        # than letting the driver fail with "syntax error near ALL".
        if op_name not in self.dialect.setop_all_support:
            raise NotImplementedError(
                f"{op_name} ALL is not supported by the current backend. "
                f"Bag semantics over {op_name} require a backend that "
                f"implements {op_name} ALL (e.g. PostgreSQL, MySQL 8+); "
                f"SQLite does not. Use a different backend or apply "
                f".bags() to a sub-expression that does not include "
                f"{op_name}."
            )
        return f"{op_name} ALL"

    def compile(self, expr: BaseRelation) -> tuple[str, list[Any]]:
        """Compile an expression to (sql_string, param_list)."""
        self.params = []
        sql = self._visit(expr)
        return sql, self.params

    def _next_alias(self) -> str:
        self._alias_counter += 1
        return f"t{self._alias_counter}"

    def _placeholder(self) -> str:
        # The just-appended parameter sits at the 0-based index
        # `len(self.params) - 1`. Dialects map that index to whichever
        # placeholder shape they need: ?, %s, :N, :pN, or %(pN)s.
        # Off-by-one here was a silent bug for paramstyles that use the
        # index — qmark/format ignore it, so SQLite never noticed, but
        # psycopg's pyformat expects %(p0)s as the first placeholder
        # and would otherwise fail with "query parameter missing: p1".
        return self.dialect.placeholder(len(self.params) - 1)

    def _qi(self, name: str) -> str:
        """Quote identifier shorthand."""
        return self.dialect.quote_identifier(name)

    # --- Source helpers ---
    # A "source" is what goes in a FROM clause: either a table name
    # or a (subquery) AS alias. These helpers avoid wrapping base
    # relations in unnecessary subqueries.

    def _as_source(self, node: BaseRelation) -> tuple[str, str]:
        """Return (from_fragment, alias) for use in FROM clauses.

        For base relations: returns ("table_name", "table_name")
        For complex expressions: returns ("(subquery) AS t1", "t1")
        """
        # KEY OPTIMIZATION POINT. Called wherever the compiler needs a
        # FROM-clause source. Without this inlining, every algebra node
        # would become a nested subquery — correct, but unreadable and
        # unlike idiomatic SQL. By returning the bare table name for a
        # Relation leaf, chains like σ(π(R)) produce a single SELECT
        # against R instead of SELECT ... FROM (SELECT ... FROM R).
        # Extending inlining to more node kinds is the main lever for
        # improving output quality.
        if isinstance(node, Relation):
            name = self._qi(node._table_name)
            return name, node._table_name
        sql = self._visit(node)
        alias = self._next_alias()
        return f"({sql}) AS {alias}", alias

    def _visit(self, node: BaseRelation) -> str:
        """Dispatch to the appropriate compile method."""
        # Set operations share one handler since their SQL structure is identical
        # (left OP right); they differ only in the keyword (UNION/INTERSECT/EXCEPT).
        if isinstance(node, (Union, Intersect, Difference)):
            return self._compile_setop(node)
        # All other nodes dispatch by lowercased class name → _compile_{name}().
        name = type(node).__name__.lower()
        method = getattr(self, f"_compile_{name}", None)
        if method is None:
            raise NotImplementedError(
                f"Compiler does not yet support {type(node).__name__}"
            )
        return method(node)

    # --- Leaf ---

    def _compile_relation(self, node: Relation) -> str:
        # DISTINCT enforces relational (set) semantics. BagWrapper sets
        # bag_mode=True at compile time, which causes _select() to drop
        # the DISTINCT keyword consistently across the whole tree.
        cols = ", ".join(self._qi(n) for n in node._schema().names())
        return f"{self._select()} {cols} FROM {self._qi(node._table_name)}"

    # --- Selection ---

    def _compile_selection(self, node: Selection) -> str:
        source, alias = self._as_source(node.child)
        where_sql = self._compile_predicate(node.predicate)
        cols = ", ".join(self._qi(n) for n in node._schema().names())
        return f"{self._select()} {cols} FROM {source} WHERE {where_sql}"

    # --- Projection ---

    def _compile_projection(self, node: Projection) -> str:
        cols = ", ".join(self._qi(n) for n in node.attrs)

        # KEY OPTIMIZATION POINT — the canonical chain-flattening pattern.
        # π(σ(R)) is the most common algebra idiom, and nesting it as
        # SELECT ... FROM (SELECT ... WHERE ...) would be visually noisy
        # for students. Collapsing into one SELECT...WHERE produces SQL
        # that mirrors how a human would write the same query.
        #
        # Tradeoff: we only peek one level down. Deeper algebra (e.g.,
        # π(σ(π(σ(R))))) still nests. Generalizing this is the obvious
        # next step for future compiler work — any similar pattern (e.g.,
        # σ(π(R)) → σ pushed into the projection's SELECT) would extend
        # the same idea.
        # Chain-flattening optimization: merge Projection(Selection(X)) into a
        # single SELECT...WHERE instead of nesting a subquery. Only one level
        # is flattened — deeper nesting still produces subqueries.
        if isinstance(node.child, Selection):
            sel = node.child
            source, alias = self._as_source(sel.child)
            where_sql = self._compile_predicate(sel.predicate)
            return f"{self._select()} {cols} FROM {source} WHERE {where_sql}"

        source, alias = self._as_source(node.child)
        return f"{self._select()} {cols} FROM {source}"

    # --- Rename ---

    def _compile_rename(self, node: Rename) -> str:
        source, alias = self._as_source(node.child)
        child_schema = node.child._schema()
        # node.mapping is {new_name: old_name}; invert so we can iterate over
        # original attributes and look up their new names. Same pattern as
        # Schema.rename().
        reverse = {old: new for new, old in node.mapping.items()}
        col_exprs = []
        for attr in child_schema.attributes:
            old = self._qi(attr.name)
            if attr.name in reverse:
                new = self._qi(reverse[attr.name])
                col_exprs.append(f"{old} AS {new}")
            else:
                col_exprs.append(old)
        cols = ", ".join(col_exprs)
        return f"{self._select()} {cols} FROM {source}"

    # --- Set Operations ---

    def _compile_setop(self, node: _SetOp) -> str:
        # No DISTINCT needed on the per-side SELECTs: SQL's UNION/INTERSECT/
        # EXCEPT are themselves set operators by default (they dedupe the
        # combined result). For bag mode, _setop_keyword switches to
        # UNION ALL / INTERSECT ALL / EXCEPT ALL so the outer operator
        # also stops deduping.
        left_sql = self._visit(node.left)
        right_sql = self._visit(node.right)
        return f"{left_sql} {self._setop_keyword(node.op_name)} {right_sql}"

    # --- Cross Product ---

    def _compile_crossproduct(self, node: CrossProduct) -> str:
        ls, la = self._as_source(node.left)
        rs, ra = self._as_source(node.right)

        # Table-qualify every column to avoid ambiguity when both sides share
        # column names. Always adds AS alias even for base tables, since both
        # sides need distinct aliases in the FROM clause.
        left_cols = [f"{la}.{self._qi(n)}" for n in node.left._schema().names()]
        right_cols = [f"{ra}.{self._qi(n)}" for n in node.right._schema().names()]
        cols = ", ".join(left_cols + right_cols)

        return f"{self._select()} {cols} FROM {ls} AS {la}, {rs} AS {ra}"

    # --- Natural Join ---

    def _compile_naturaljoin(self, node: NaturalJoin) -> str:
        ls, la = self._as_source(node.left)
        rs, ra = self._as_source(node.right)

        # Emit explicit JOIN...ON rather than SQL's NATURAL JOIN. Deliberate
        # for teaching — students see exactly which columns are being matched.
        common = node.left._schema().common(node.right._schema())
        common_names = common.names()

        # Build ON clause — common attributes taken from left side, matching
        # the schema rule in Schema.join_compose().
        on_parts = [
            f"{la}.{self._qi(n)} = {ra}.{self._qi(n)}"
            for n in common_names
        ]
        on_clause = " AND ".join(on_parts)

        # Select list MUST be driven by node._schema().names(), not by
        # (common + left_only + right_only). Schema.join_compose preserves
        # left-side ordering — common attributes keep their original position
        # in the left schema — so iterating left_only/right_only would put
        # columns in a different order than the schema reports. Downstream
        # consumers (str(), collect() indexing, render_table headers) trust
        # schema().names(); SQL must match.
        left_names = set(node.left._schema().names())
        cols = []
        for n in node._schema().names():
            q = self._qi(n)
            if n in left_names:
                # Common attributes live in the left schema, so they end up
                # qualified by the left alias here — same convention as the
                # ON clause and as Schema.join_compose's left-wins rule.
                cols.append(f"{la}.{q}")
            else:
                cols.append(f"{ra}.{q}")
        col_list = ", ".join(cols)

        # Use table names directly if base relations
        left_from = self._from_with_alias(node.left, ls, la)
        right_from = self._from_with_alias(node.right, rs, ra)

        return (
            f"{self._select()} {col_list} "
            f"FROM {left_from} "
            f"JOIN {right_from} ON {on_clause}"
        )

    def _from_with_alias(self, node: BaseRelation, source: str, alias: str) -> str:
        """Format a FROM source, adding AS alias only when needed."""
        if isinstance(node, Relation):
            # Avoid redundant "table AS table" when alias matches the table name.
            if node._table_name == alias:
                return source
            return f"{source} AS {alias}"
        # Subqueries from _as_source() already include "(...) AS tN".
        return source

    # --- Theta Join ---

    def _compile_thetajoin(self, node: ThetaJoin) -> str:
        ls, la = self._as_source(node.left)
        rs, ra = self._as_source(node.right)

        left_cols = [f"{la}.{self._qi(n)}" for n in node.left._schema().names()]
        right_cols = [f"{ra}.{self._qi(n)}" for n in node.right._schema().names()]
        cols = ", ".join(left_cols + right_cols)

        on_sql = self._compile_predicate(
            node.predicate, la, ra,
            node.left._schema(), node.right._schema()
        )

        left_from = self._from_with_alias(node.left, ls, la)
        right_from = self._from_with_alias(node.right, rs, ra)

        return (
            f"{self._select()} {cols} "
            f"FROM {left_from} "
            f"JOIN {right_from} ON {on_sql}"
        )

    # --- Equijoin ---

    def _compile_equijoin(self, node: Equijoin) -> str:
        ls, la = self._as_source(node.left)
        rs, ra = self._as_source(node.right)

        left_cols = [f"{la}.{self._qi(n)}" for n in node.left._schema().names()]
        right_cols = [
            f"{ra}.{self._qi(n)}"
            for n in node.right._schema().names()
            if n != node.right_attr
        ]
        cols = ", ".join(left_cols + right_cols)

        left_from = self._from_with_alias(node.left, ls, la)
        right_from = self._from_with_alias(node.right, rs, ra)

        return (
            f"{self._select()} {cols} "
            f"FROM {left_from} "
            f"JOIN {right_from} "
            f"ON {la}.{self._qi(node.left_attr)} = {ra}.{self._qi(node.right_attr)}"
        )

    # --- Semijoin ---

    def _compile_semijoin(self, node: Semijoin) -> str:
        # Standard SQL translation: EXISTS with a correlated subquery.
        ls, la = self._as_source(node.left)
        rs, ra = self._as_source(node.right)

        common = node.left._schema().common(node.right._schema())
        corr = " AND ".join(
            f"{la}.{self._qi(n)} = {ra}.{self._qi(n)}"
            for n in common.names()
        )
        cols = ", ".join(f"{la}.{self._qi(n)}" for n in node.left._schema().names())

        left_from = self._from_with_alias(node.left, ls, la)
        right_from = self._from_with_alias(node.right, rs, ra)

        return (
            f"{self._select()} {cols} FROM {left_from} "
            f"WHERE EXISTS (SELECT 1 FROM {right_from} WHERE {corr})"
        )

    # --- Antijoin ---

    def _compile_antijoin(self, node: Antijoin) -> str:
        # Standard SQL translation: NOT EXISTS with a correlated subquery.
        ls, la = self._as_source(node.left)
        rs, ra = self._as_source(node.right)

        common = node.left._schema().common(node.right._schema())
        corr = " AND ".join(
            f"{la}.{self._qi(n)} = {ra}.{self._qi(n)}"
            for n in common.names()
        )
        cols = ", ".join(f"{la}.{self._qi(n)}" for n in node.left._schema().names())

        left_from = self._from_with_alias(node.left, ls, la)
        right_from = self._from_with_alias(node.right, rs, ra)

        return (
            f"{self._select()} {cols} FROM {left_from} "
            f"WHERE NOT EXISTS (SELECT 1 FROM {right_from} WHERE {corr})"
        )

    # --- Outer Join ---

    def _compile_outerjoin(self, node: OuterJoin) -> str:
        ls, la = self._as_source(node.left)
        rs, ra = self._as_source(node.right)

        common = node.left._schema().common(node.right._schema())
        common_names = common.names()
        common_set = set(common_names)

        join_type = {
            "left": "LEFT OUTER",
            "right": "RIGHT OUTER",
            "full": "FULL OUTER",
        }[node.how]

        on_parts = [
            f"{la}.{self._qi(n)} = {ra}.{self._qi(n)}"
            for n in common_names
        ]
        on_clause = " AND ".join(on_parts)

        # COALESCE for common attributes: in an outer join, a common attribute
        # may be NULL on one side. COALESCE picks the non-NULL value, matching
        # relational algebra semantics where the join attribute is always present.
        #
        # As in _compile_naturaljoin above, the SELECT order MUST follow
        # node._schema().names() rather than (common + left_only + right_only).
        # Schema.join_compose preserves left-side positions for common attrs,
        # so any other iteration order would silently desync columns from the
        # reported schema.
        left_names = set(node.left._schema().names())
        cols = []
        for n in node._schema().names():
            q = self._qi(n)
            if n in common_set:
                cols.append(f"COALESCE({la}.{q}, {ra}.{q}) AS {q}")
            elif n in left_names:
                cols.append(f"{la}.{q}")
            else:
                cols.append(f"{ra}.{q}")
        col_list = ", ".join(cols)

        left_from = self._from_with_alias(node.left, ls, la)
        right_from = self._from_with_alias(node.right, rs, ra)

        return (
            f"{self._select()} {col_list} "
            f"FROM {left_from} "
            f"{join_type} JOIN {right_from} ON {on_clause}"
        )

    # --- Grouping ---

    def _compile_grouping(self, node: Grouping) -> str:
        # Intentionally omits DISTINCT — GROUP BY already produces one row per
        # group, so DISTINCT would be redundant overhead.
        source, alias = self._as_source(node.child)
        key_cols = [self._qi(k) for k in node.keys]
        agg_exprs = []
        for name, agg in node.aggs.items():
            attr = self._qi(agg.attr) if agg.attr != "*" else "*"
            agg_exprs.append(f"{agg.func}({attr}) AS {self._qi(name)}")

        select_clause = ", ".join(key_cols + agg_exprs)
        sql = f"SELECT {select_clause} FROM {source}"
        if key_cols:
            sql += f" GROUP BY {', '.join(key_cols)}"
        return sql

    # --- Division ---

    def _compile_division(self, node: Division) -> str:
        # DOCUMENTED INVARIANT EXCEPTION: the compiler visits each node once
        # per compile — except here. Relational division has no single-SELECT
        # SQL equivalent; the standard encoding is a correlated
        # NOT EXISTS ( divisor EXCEPT (dividend-rows-for-this-key) ) which
        # requires two independent references to the dividend subtree with
        # distinct aliases. We therefore call _visit on node.left (or emit
        # its table name) a second time below. Re-visiting is safe because
        # _visit is pure w.r.t. the tree; the only side effect is appending
        # to self.params, and re-binding literals for the inner reference
        # is correct — each occurrence of a literal needs its own placeholder.
        # Division uses the "NOT EXISTS (divisor EXCEPT correlated dividend)"
        # pattern. This is the only operation that visits the same subtree
        # (the dividend) twice — once for the outer FROM, once for the inner
        # correlated subquery. See the module-level invariants at the top
        # of this file for the broader contract.
        ls, la = self._as_source(node.left)
        result_attrs = node._schema().names()
        divisor_attrs = node.right._schema().names()

        dividend_alias = la
        inner_alias = self._next_alias()

        result_cols = ", ".join(
            f"{dividend_alias}.{self._qi(n)}" for n in result_attrs
        )

        # Inner correlated subquery
        correlation = " AND ".join(
            f"{inner_alias}.{self._qi(n)} = {dividend_alias}.{self._qi(n)}"
            for n in result_attrs
        )
        inner_divisor_cols = ", ".join(
            f"{inner_alias}.{self._qi(n)}" for n in divisor_attrs
        )

        # Compile the divisor (right side).
        right_sql = self._visit(node.right)
        # Every derived table in a FROM clause needs an alias for portability.
        # SQLite tolerates unaliased subqueries; PostgreSQL ("subquery in FROM
        # must have an alias") and several other backends do not. This alias
        # is purely structural — the inner SELECT references divisor columns
        # unqualified, which works because there is no other source in scope.
        divisor_alias = self._next_alias()

        # The dividend must be visited a second time for the inner correlated
        # subquery — it needs its own alias distinct from the outer reference.
        if isinstance(node.left, Relation):
            inner_from = f"{self._qi(node.left._table_name)} AS {inner_alias}"
        else:
            left_sql_inner = self._visit(node.left)
            inner_from = f"({left_sql_inner}) AS {inner_alias}"

        dividend_from = self._from_with_alias(node.left, ls, dividend_alias)

        return (
            f"{self._select()} {result_cols} "
            f"FROM {dividend_from} "
            f"WHERE NOT EXISTS ("
            f"SELECT {', '.join(self._qi(n) for n in divisor_attrs)} "
            f"FROM ({right_sql}) AS {divisor_alias} "
            f"EXCEPT "
            f"SELECT {inner_divisor_cols} "
            f"FROM {inner_from} "
            f"WHERE {correlation})"
        )

    # --- Predicate compilation ---

    def _compile_predicate(
        self,
        pred: Any,
        left_alias: str | None = None,
        right_alias: str | None = None,
        left_schema: Any = None,
        right_schema: Any = None,
    ) -> str:
        # Recursively compiles predicate trees into SQL WHERE/ON fragments.
        # The alias/schema params are only non-None for join predicates, where
        # column references must be table-qualified to resolve ambiguity.
        if isinstance(pred, Predicate):
            from .predicates import SQL_OPERATORS
            left_sql = self._compile_pred_operand(
                pred.left, left_alias, right_alias, left_schema, right_schema
            )
            right_sql = self._compile_pred_operand(
                pred.right, left_alias, right_alias, left_schema, right_schema
            )
            return f"{left_sql} {SQL_OPERATORS[pred.op]} {right_sql}"

        elif isinstance(pred, CompoundPredicate):
            left_sql = self._compile_predicate(
                pred.left, left_alias, right_alias, left_schema, right_schema
            )
            right_sql = self._compile_predicate(
                pred.right, left_alias, right_alias, left_schema, right_schema
            )
            return f"({left_sql} {pred.op} {right_sql})"

        elif isinstance(pred, NotPredicate):
            inner = self._compile_predicate(
                pred.operand, left_alias, right_alias, left_schema, right_schema
            )
            return f"NOT ({inner})"

        raise TypeError(f"Unknown predicate type: {type(pred)}")

    def _compile_pred_operand(
        self,
        operand: Any,
        left_alias: str | None = None,
        right_alias: str | None = None,
        left_schema: Any = None,
        right_schema: Any = None,
    ) -> str:
        if isinstance(operand, Literal):
            # HARD INVARIANT: every literal value is bound as a parameter.
            # No path through this compiler may str-format a user value
            # into SQL text. This is both a security rule (prevents
            # injection) and a teaching rule (the emitted SQL should show
            # placeholders, matching how real application code is written).
            # Literal values become parameterized placeholders — never
            # interpolated into SQL. Append first, then emit placeholder
            # (placeholder index is based on current params length).
            self.params.append(operand.value)
            return self._placeholder()
        elif isinstance(operand, Attr):
            qname = self._qi(operand.name)
            # For join predicates, table-qualify the column by checking which
            # side's schema contains it. For simple selections (aliases are
            # None), the column is emitted unqualified.
            if left_alias and right_alias and left_schema and right_schema:
                if operand.name in left_schema:
                    return f"{left_alias}.{qname}"
                elif operand.name in right_schema:
                    return f"{right_alias}.{qname}"
            return qname
        raise TypeError(f"Unknown operand type: {type(operand)}")
