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
    BaseRelation,
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

    def __init__(self, dialect: Dialect):
        self.dialect = dialect
        # Flat list accumulating all query parameters across the entire tree.
        # Positional placeholders in the SQL correspond 1:1 to entries here.
        self.params: list = []
        # Monotonic counter for generating unique subquery aliases (t1, t2, ...).
        self._alias_counter = 0

    def compile(self, expr: BaseRelation) -> tuple[str, list]:
        """Compile an expression to (sql_string, param_list)."""
        self.params = []
        sql = self._visit(expr)
        return sql, self.params

    def _next_alias(self) -> str:
        self._alias_counter += 1
        return f"t{self._alias_counter}"

    def _placeholder(self) -> str:
        # Uses current param count as index — must be called *after* appending
        # the value to self.params. Dialects use this to emit ?, %s, or :N.
        return self.dialect.placeholder(len(self.params))

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
        # improving output quality; see CLAUDE.md.
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
        # DISTINCT enforces relational (set) semantics. BagWrapper strips it
        # via string replacement when bag semantics are desired.
        cols = ", ".join(self._qi(n) for n in node._schema().names())
        return f"SELECT DISTINCT {cols} FROM {self._qi(node._table_name)}"

    # --- Selection ---

    def _compile_selection(self, node: Selection) -> str:
        source, alias = self._as_source(node.child)
        where_sql = self._compile_predicate(node.predicate)
        cols = ", ".join(self._qi(n) for n in node._schema().names())
        return f"SELECT DISTINCT {cols} FROM {source} WHERE {where_sql}"

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
        # π(σ(π(σ(R))))) still nests. Generalizing this is the template
        # CLAUDE.md points at for future compiler work — any similar
        # pattern (e.g., σ(π(R)) → σ pushed into the projection's SELECT)
        # would extend the same idea.
        # Chain-flattening optimization: merge Projection(Selection(X)) into a
        # single SELECT...WHERE instead of nesting a subquery. Only one level
        # is flattened — deeper nesting still produces subqueries.
        if isinstance(node.child, Selection):
            sel = node.child
            source, alias = self._as_source(sel.child)
            where_sql = self._compile_predicate(sel.predicate)
            return f"SELECT DISTINCT {cols} FROM {source} WHERE {where_sql}"

        source, alias = self._as_source(node.child)
        return f"SELECT DISTINCT {cols} FROM {source}"

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
        return f"SELECT DISTINCT {cols} FROM {source}"

    # --- Set Operations ---

    def _compile_setop(self, node: _SetOp) -> str:
        # No DISTINCT needed: SQL's UNION/INTERSECT/EXCEPT are set operations
        # by default (UNION ALL would be the bag variant).
        left_sql = self._visit(node.left)
        right_sql = self._visit(node.right)
        return f"{left_sql} {node.op_name} {right_sql}"

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

        return f"SELECT DISTINCT {cols} FROM {ls} AS {la}, {rs} AS {ra}"

    # --- Natural Join ---

    def _compile_naturaljoin(self, node: NaturalJoin) -> str:
        ls, la = self._as_source(node.left)
        rs, ra = self._as_source(node.right)

        # Emit explicit JOIN...ON rather than SQL's NATURAL JOIN. Deliberate
        # for teaching — students see exactly which columns are being matched.
        common = node.left._schema().common(node.right._schema())
        common_names = common.names()
        common_set = set(common_names)

        # Build ON clause — common attributes taken from left side, matching
        # the schema rule in Schema.join_compose().
        on_parts = [
            f"{la}.{self._qi(n)} = {ra}.{self._qi(n)}"
            for n in common_names
        ]
        on_clause = " AND ".join(on_parts)

        # Select list: common from left, rest of left, rest of right
        cols = []
        for n in common_names:
            cols.append(f"{la}.{self._qi(n)}")
        for n in node.left._schema().names():
            if n not in common_set:
                cols.append(f"{la}.{self._qi(n)}")
        for n in node.right._schema().names():
            if n not in common_set:
                cols.append(f"{ra}.{self._qi(n)}")
        col_list = ", ".join(cols)

        # Use table names directly if base relations
        left_from = self._from_with_alias(node.left, ls, la)
        right_from = self._from_with_alias(node.right, rs, ra)

        return (
            f"SELECT DISTINCT {col_list} "
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
            f"SELECT DISTINCT {cols} "
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
            f"SELECT DISTINCT {cols} "
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
            f"SELECT DISTINCT {cols} FROM {left_from} "
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
            f"SELECT DISTINCT {cols} FROM {left_from} "
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
        cols = []
        for n in common_names:
            q = self._qi(n)
            cols.append(f"COALESCE({la}.{q}, {ra}.{q}) AS {q}")
        for n in node.left._schema().names():
            if n not in common_set:
                cols.append(f"{la}.{self._qi(n)}")
        for n in node.right._schema().names():
            if n not in common_set:
                cols.append(f"{ra}.{self._qi(n)}")
        col_list = ", ".join(cols)

        left_from = self._from_with_alias(node.left, ls, la)
        right_from = self._from_with_alias(node.right, rs, ra)

        return (
            f"SELECT DISTINCT {col_list} "
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
        # correlated subquery. See CLAUDE.md invariants.
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

        # The dividend must be visited a second time for the inner correlated
        # subquery — it needs its own alias distinct from the outer reference.
        if isinstance(node.left, Relation):
            inner_from = f"{self._qi(node.left._table_name)} AS {inner_alias}"
        else:
            left_sql_inner = self._visit(node.left)
            inner_from = f"({left_sql_inner}) AS {inner_alias}"

        dividend_from = self._from_with_alias(node.left, ls, dividend_alias)

        return (
            f"SELECT DISTINCT {result_cols} "
            f"FROM {dividend_from} "
            f"WHERE NOT EXISTS ("
            f"SELECT {', '.join(self._qi(n) for n in divisor_attrs)} "
            f"FROM ({right_sql}) "
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
