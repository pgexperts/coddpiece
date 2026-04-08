"""Relation types and expression tree nodes.

Every relational algebra expression is a tree of BaseRelation nodes.
Leaf nodes are Relation objects (backed by a real table).
Interior nodes are operations (Selection, Projection, etc.).

All operations return new nodes — nothing mutates.
Nothing hits the database until .collect() or __iter__.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .errors import AttributeError_, EngineError, SchemaError
from .predicates import Attr, PredicateNode
from .schema import Schema

if TYPE_CHECKING:
    from .engine import Engine


class BaseRelation(ABC):
    """Abstract base for all relation-valued expressions.

    This is the heart of the package. Every operation returns a new
    BaseRelation, enabling method chaining and lazy evaluation.
    """

    @property
    @abstractmethod
    def _engine(self) -> Engine:
        """The engine that will execute this expression."""

    @abstractmethod
    def _schema(self) -> Schema:
        """Compute the output schema for this expression."""

    @property
    @abstractmethod
    def relation_name(self) -> str:
        """A short name for display purposes."""

    def schema(self) -> Schema:
        """The output schema of this expression."""
        return self._schema()

    # --- Attr proxy access ---

    def __getattr__(self, name: str) -> Attr:
        # Guard against internal attribute lookups (e.g. _schema, __class__).
        # Without this, Python's own machinery would recurse into _schema()
        # during normal attribute resolution, causing infinite recursion.
        if name.startswith("_"):
            raise AttributeError(name)
        # Check the attribute exists in our schema
        schema = self._schema()
        if name not in schema:
            raise AttributeError_(
                f"Relation {self.relation_name!r} has no attribute {name!r}. "
                f"Available: {', '.join(schema.names())}"
            )
        return Attr(self, name)

    def attr(self, name: str) -> Attr:
        """Explicit attribute access (escape hatch for name collisions)."""
        # Use this when a column name shadows a method (e.g., "select", "count").
        # Same validation as __getattr__ but callable explicitly.
        schema = self._schema()
        if name not in schema:
            raise AttributeError_(
                f"Relation {self.relation_name!r} has no attribute {name!r}. "
                f"Available: {', '.join(schema.names())}"
            )
        return Attr(self, name)

    # --- Core operations ---

    def select(self, predicate: PredicateNode) -> Selection:
        """σ (Selection): Keep only rows satisfying the predicate."""
        return Selection(self, predicate)

    def project(self, *attrs: str) -> Projection:
        """π (Projection): Keep only the named columns."""
        return Projection(self, attrs)

    def rename(self, **mapping: str) -> Rename:
        """ρ (Rename): Rename attributes. Usage: .rename(new_name='old_name')"""
        return Rename(self, mapping)

    def cross(self, other: BaseRelation) -> CrossProduct:
        """× (Cross Product): Cartesian product of two relations."""
        return CrossProduct(self, other)

    def join(self, other: BaseRelation) -> NaturalJoin:
        """⋈ (Natural Join): Join on all common attribute names."""
        return NaturalJoin(self, other)

    def union(self, other: BaseRelation) -> Union:
        """∪ (Union): All tuples in either relation (set semantics)."""
        return Union(self, other)

    def intersect(self, other: BaseRelation) -> Intersect:
        """∩ (Intersect): Tuples in both relations."""
        return Intersect(self, other)

    def difference(self, other: BaseRelation) -> Difference:
        """− (Difference): Tuples in self but not in other."""
        return Difference(self, other)

    # Extended operations — stubs for later phases
    def theta_join(self, other: BaseRelation, predicate: PredicateNode) -> ThetaJoin:
        """⋈θ (Theta Join): Join with an arbitrary predicate."""
        return ThetaJoin(self, other, predicate)

    def equijoin(self, other: BaseRelation, left_attr: str, right_attr: str) -> Equijoin:
        """⋈= (Equijoin): Join where left_attr = right_attr."""
        return Equijoin(self, other, left_attr, right_attr)

    def semijoin(self, other: BaseRelation) -> Semijoin:
        """⋉ (Semijoin): Left tuples that have a match in right."""
        return Semijoin(self, other)

    def antijoin(self, other: BaseRelation) -> Antijoin:
        """▷ (Antijoin): Left tuples with NO match in right."""
        return Antijoin(self, other)

    def outer_join(self, other: BaseRelation, how: str = "full") -> OuterJoin:
        """⟕⟖⟗ (Outer Join): Join preserving unmatched tuples."""
        return OuterJoin(self, other, how)

    def divide(self, other: BaseRelation) -> Division:
        """÷ (Division): Tuples associated with ALL tuples in other."""
        return Division(self, other)

    def group(self, *keys: str, **aggs: Any) -> Grouping:
        """γ (Grouping/Aggregation): Group by keys, compute aggregates."""
        return Grouping(self, keys, aggs)

    # --- Materialization ---

    def collect(self) -> list[tuple]:
        """Execute the expression and return all rows as a list of tuples."""
        return self._engine.execute(self)

    def __iter__(self):
        """Lazily iterate over result rows."""
        return iter(self.collect())

    def count(self) -> int:
        """Execute and return the row count."""
        return len(self.collect())

    # --- Display ---

    def algebra(self) -> str:
        """Render as relational algebra notation."""
        # Lazy import: display.py imports node types from this module, so
        # importing display at module level would create a circular dependency.
        from .display import render_algebra
        return render_algebra(self)

    def sql(self) -> str:
        """Render the SQL that would be executed."""
        # Lazy imports to break circular dependency (same reason as algebra()).
        from .compiler import Compiler
        from .display import format_sql
        sql, params = Compiler(self._engine.dialect).compile(self)
        sql = format_sql(sql)
        if params:
            sql += f"\n-- params: {params}"
        return sql

    def tree(self) -> str:
        """Render as an indented expression tree."""
        from .display import render_tree
        return render_tree(self)

    def explain(self) -> str:
        """Show algebra, tree, SQL, and natural-language reading."""
        from .display import render_explain
        return render_explain(self)

    def bags(self) -> BagWrapper:
        """Switch to bag semantics (preserve duplicates).

        The relational model uses set semantics (no duplicates).
        SQL defaults to bag semantics. Use .bags() to see the difference.
        """
        return BagWrapper(self)

    def __repr__(self) -> str:
        return f"<{type(self).__name__}: {self.relation_name} {self._schema()}>"

    def __str__(self) -> str:
        """Pretty-print the relation as a table."""
        from .display import render_table
        return render_table(self)


class BagWrapper:
    """Wrapper that executes with bag semantics (no DISTINCT).

    This is a teaching tool: it lets students see how SQL's default
    bag semantics differ from the relational model's set semantics.
    """

    def __init__(self, expr: BaseRelation):
        self._expr = expr

    def collect(self) -> list[tuple]:
        from .compiler import Compiler
        compiler = Compiler(self._expr._engine.dialect)
        sql, params = compiler.compile(self._expr)
        # Intentionally crude string replacement. A production system would
        # thread a bag/set flag through the compiler, but this works because
        # the compiler always emits "SELECT DISTINCT" and never nests DISTINCT
        # in a way that partial replacement would corrupt the query.
        sql = sql.replace("SELECT DISTINCT ", "SELECT ")
        formatted = self._expr._engine.dialect.format_params(params)
        cursor = self._expr._engine.connection.cursor()
        cursor.execute(sql, formatted)
        return cursor.fetchall()

    def __iter__(self):
        return iter(self.collect())

    def count(self) -> int:
        return len(self.collect())

    def explain(self) -> str:
        from .display import render_explain
        base = render_explain(self._expr)
        note = (
            "\nNote: Bag semantics (duplicates preserved). SQL defaults to bags;\n"
            "  the relational model uses sets. This query omits DISTINCT."
        )
        return base + note


def _check_same_engine(left: BaseRelation, right: BaseRelation) -> Engine:
    """Validate two relations share an engine; return it."""
    # Identity check (`is`), not equality — two Engine objects pointing at the
    # same database are NOT interchangeable because they may hold different
    # transaction state. Operations must share the exact same Engine instance.
    if left._engine is not right._engine:
        raise EngineError(
            "Cannot combine relations from different engines. "
            "Both operands must be backed by the same database connection."
        )
    return left._engine


# ---------------------------------------------------------------------------
# Leaf node
# ---------------------------------------------------------------------------


class Relation(BaseRelation):
    """A base relation backed by a real database table."""

    def __init__(self, engine: Engine, table_name: str, schema: Schema):
        # Double-underscore names trigger Python name mangling, preventing
        # accidental override in subclasses. Only leaf Relation nodes store
        # their own engine/schema; all expression nodes derive theirs from
        # their children.
        self.__engine = engine
        self._table_name = table_name
        self.__schema = schema

    @property
    def _engine(self) -> Engine:
        return self.__engine

    def _schema(self) -> Schema:
        return self.__schema

    @property
    def relation_name(self) -> str:
        return self._table_name


# ---------------------------------------------------------------------------
# Phase 1 expression nodes: Selection, Projection
# ---------------------------------------------------------------------------


# frozen=True: immutability is critical because expression trees may share
# subtrees across multiple expressions, and the compiler assumes the tree
# is stable during traversal.
# repr=False: inherit BaseRelation.__repr__ (shows class name, relation_name,
# schema) instead of the dataclass default which would expose internal fields.
@dataclass(frozen=True, repr=False)
class Selection(BaseRelation):
    """σ (Selection): Filter rows by a predicate."""

    child: BaseRelation
    predicate: PredicateNode

    @property
    def _engine(self) -> Engine:
        return self.child._engine

    def _schema(self) -> Schema:
        return self.child._schema()

    @property
    def relation_name(self) -> str:
        return self.child.relation_name


@dataclass(frozen=True, repr=False)
class Projection(BaseRelation):
    """π (Projection): Keep only specified attributes."""

    child: BaseRelation
    attrs: tuple[str, ...]

    def __post_init__(self):
        # Fail-fast validation: schema errors are caught at tree construction,
        # not deferred to compilation or execution. This is a project invariant.
        self.child._schema().project(*self.attrs)

    @property
    def _engine(self) -> Engine:
        return self.child._engine

    def _schema(self) -> Schema:
        return self.child._schema().project(*self.attrs)

    @property
    def relation_name(self) -> str:
        return self.child.relation_name


# ---------------------------------------------------------------------------
# Phase 2: Rename, Union, Intersect, Difference
# ---------------------------------------------------------------------------


@dataclass(frozen=True, repr=False)
class Rename(BaseRelation):
    """ρ (Rename): Rename attributes. mapping is {new_name: old_name}."""

    child: BaseRelation
    mapping: dict[str, str]

    def __post_init__(self):
        # Fail-fast: same eager validation pattern as Projection above.
        self.child._schema().rename(**self.mapping)

    @property
    def _engine(self) -> Engine:
        return self.child._engine

    def _schema(self) -> Schema:
        return self.child._schema().rename(**self.mapping)

    @property
    def relation_name(self) -> str:
        return self.child.relation_name


class _SetOp(BaseRelation):
    """Base class for Union, Intersect, Difference.

    All three share the same validation (same engine, compatible schemas)
    and the same schema rule (left schema wins).
    """

    def __init__(self, left: BaseRelation, right: BaseRelation, op_name: str):
        self.left = left
        self.right = right
        # op_name stores the SQL keyword directly ("UNION", "INTERSECT",
        # "EXCEPT") since it is used verbatim in SQL generation.
        self.op_name = op_name
        _check_same_engine(left, right)
        if not left._schema().compatible(right._schema()):
            raise SchemaError(
                f"{op_name.upper()} requires identical schemas.\n\n"
                f"  Left ({left.relation_name}):  {left._schema()}\n"
                f"  Right ({right.relation_name}): {right._schema()}\n\n"
                f"{left._schema().diff(right._schema())}\n\n"
                f"  Hint: Use PROJECT to align schemas before {op_name.upper()}."
            )

    @property
    def _engine(self) -> Engine:
        return self.left._engine

    def _schema(self) -> Schema:
        return self.left._schema()

    @property
    def relation_name(self) -> str:
        return f"({self.left.relation_name} {self.op_name} {self.right.relation_name})"


class Union(_SetOp):
    """∪ (Union): All tuples in either relation."""

    def __init__(self, left: BaseRelation, right: BaseRelation):
        super().__init__(left, right, "UNION")


class Intersect(_SetOp):
    """∩ (Intersect): Tuples in both relations."""

    def __init__(self, left: BaseRelation, right: BaseRelation):
        super().__init__(left, right, "INTERSECT")


class Difference(_SetOp):
    """− (Difference): Tuples in left but not in right."""

    def __init__(self, left: BaseRelation, right: BaseRelation):
        # Relational algebra calls it "difference"; SQL calls it EXCEPT.
        super().__init__(left, right, "EXCEPT")


# ---------------------------------------------------------------------------
# Phase 3: CrossProduct, NaturalJoin, ThetaJoin, Equijoin
# ---------------------------------------------------------------------------


class CrossProduct(BaseRelation):
    """× (Cross Product): Cartesian product."""

    def __init__(self, left: BaseRelation, right: BaseRelation):
        self.left = left
        self.right = right
        _check_same_engine(left, right)
        # Validate no name collisions (compose will raise if so)
        left._schema().compose(right._schema())

    @property
    def _engine(self) -> Engine:
        return self.left._engine

    def _schema(self) -> Schema:
        return self.left._schema().compose(self.right._schema())

    @property
    def relation_name(self) -> str:
        return f"({self.left.relation_name} × {self.right.relation_name})"


class NaturalJoin(BaseRelation):
    """⋈ (Natural Join): Join on common attributes."""

    def __init__(self, left: BaseRelation, right: BaseRelation):
        self.left = left
        self.right = right
        _check_same_engine(left, right)
        common = left._schema().common(right._schema())
        # Guardrail: a natural join with no common attributes silently becomes
        # a cross product, which is almost certainly a mistake. We force the
        # user to be explicit by using equijoin or rename instead.
        if len(common) == 0:
            raise SchemaError(
                f"NATURAL JOIN between {left.relation_name!r} and "
                f"{right.relation_name!r} found no common attributes.\n\n"
                f"  Left:  {left._schema()}\n"
                f"  Right: {right._schema()}\n\n"
                f"  Hint: Use EQUIJOIN to specify the join condition explicitly:\n"
                f"    left.equijoin(right, 'left_attr', 'right_attr')\n"
                f"  Or RENAME to align attribute names first."
            )

    @property
    def _engine(self) -> Engine:
        return self.left._engine

    def _schema(self) -> Schema:
        return self.left._schema().join_compose(self.right._schema())

    @property
    def relation_name(self) -> str:
        return f"({self.left.relation_name} ⋈ {self.right.relation_name})"


class ThetaJoin(BaseRelation):
    """⋈θ (Theta Join): Join with an arbitrary predicate."""

    def __init__(self, left: BaseRelation, right: BaseRelation, predicate: PredicateNode):
        self.left = left
        self.right = right
        self.predicate = predicate
        _check_same_engine(left, right)
        # Schema is full compose (must have disjoint names)
        left._schema().compose(right._schema())

    @property
    def _engine(self) -> Engine:
        return self.left._engine

    def _schema(self) -> Schema:
        return self.left._schema().compose(self.right._schema())

    @property
    def relation_name(self) -> str:
        return f"({self.left.relation_name} ⋈θ {self.right.relation_name})"


class Equijoin(BaseRelation):
    """⋈= (Equijoin): Join where left_attr = right_attr."""

    def __init__(self, left: BaseRelation, right: BaseRelation,
                 left_attr: str, right_attr: str):
        self.left = left
        self.right = right
        self.left_attr = left_attr
        self.right_attr = right_attr
        _check_same_engine(left, right)
        # Validate attributes exist
        if left_attr not in left._schema():
            raise AttributeError_(
                f"Left relation has no attribute {left_attr!r}. "
                f"Available: {', '.join(left._schema().names())}"
            )
        if right_attr not in right._schema():
            raise AttributeError_(
                f"Right relation has no attribute {right_attr!r}. "
                f"Available: {', '.join(right._schema().names())}"
            )

    @property
    def _engine(self) -> Engine:
        return self.left._engine

    def _schema(self) -> Schema:
        # Unlike cross product, equijoin drops the duplicate join column from
        # the right side (since left_attr = right_attr, keeping both is
        # redundant). Then we check for remaining name collisions.
        from .schema import Attribute
        left_s = self.left._schema()
        right_s = self.right._schema()
        right_attrs = tuple(
            a for a in right_s.attributes if a.name != self.right_attr
        )
        left_names = set(left_s.names())
        collisions = {a.name for a in right_attrs} & left_names
        if collisions:
            raise SchemaError(
                f"EQUIJOIN result has ambiguous attribute names: {collisions}. "
                f"Hint: Use RENAME on one relation first."
            )
        return Schema(left_s.attributes + right_attrs)

    @property
    def relation_name(self) -> str:
        return f"({self.left.relation_name} ⋈ {self.right.relation_name})"


# ---------------------------------------------------------------------------
# Phase 4: Semijoin, Antijoin, OuterJoin
# ---------------------------------------------------------------------------


class Semijoin(BaseRelation):
    """⋉ (Semijoin): Left tuples that have a match in right."""

    def __init__(self, left: BaseRelation, right: BaseRelation):
        self.left = left
        self.right = right
        _check_same_engine(left, right)
        common = left._schema().common(right._schema())
        if len(common) == 0:
            raise SchemaError(
                f"SEMIJOIN requires common attributes. "
                f"None found between {left.relation_name!r} and {right.relation_name!r}."
            )

    @property
    def _engine(self) -> Engine:
        return self.left._engine

    def _schema(self) -> Schema:
        return self.left._schema()

    @property
    def relation_name(self) -> str:
        return f"({self.left.relation_name} ⋉ {self.right.relation_name})"


class Antijoin(BaseRelation):
    """▷ (Antijoin): Left tuples with NO match in right."""

    def __init__(self, left: BaseRelation, right: BaseRelation):
        self.left = left
        self.right = right
        _check_same_engine(left, right)
        common = left._schema().common(right._schema())
        if len(common) == 0:
            raise SchemaError(
                f"ANTIJOIN requires common attributes. "
                f"None found between {left.relation_name!r} and {right.relation_name!r}."
            )

    @property
    def _engine(self) -> Engine:
        return self.left._engine

    def _schema(self) -> Schema:
        return self.left._schema()

    @property
    def relation_name(self) -> str:
        return f"({self.left.relation_name} ▷ {self.right.relation_name})"


class OuterJoin(BaseRelation):
    """⟕⟖⟗ (Outer Join): Join preserving unmatched tuples."""

    VALID_HOW = ("left", "right", "full")

    def __init__(self, left: BaseRelation, right: BaseRelation, how: str = "full"):
        if how not in self.VALID_HOW:
            raise ValueError(f"'how' must be one of {self.VALID_HOW}, got {how!r}")
        self.left = left
        self.right = right
        self.how = how
        _check_same_engine(left, right)
        common = left._schema().common(right._schema())
        if len(common) == 0:
            raise SchemaError(
                f"OUTER JOIN requires common attributes. "
                f"None found between {left.relation_name!r} and {right.relation_name!r}."
            )

    @property
    def _engine(self) -> Engine:
        return self.left._engine

    def _schema(self) -> Schema:
        return self.left._schema().join_compose(self.right._schema())

    @property
    def relation_name(self) -> str:
        sym = {"left": "⟕", "right": "⟖", "full": "⟗"}[self.how]
        return f"({self.left.relation_name} {sym} {self.right.relation_name})"


# ---------------------------------------------------------------------------
# Phase 5: Grouping
# ---------------------------------------------------------------------------


class Grouping(BaseRelation):
    """γ (Grouping/Aggregation)."""

    def __init__(self, child: BaseRelation, keys: tuple[str, ...], aggs: dict[str, Any]):
        self.child = child
        self.keys = keys
        self.aggs = aggs
        # Validate keys exist
        schema = child._schema()
        for k in keys:
            if k not in schema:
                raise AttributeError_(
                    f"Grouping key {k!r} not in schema. "
                    f"Available: {', '.join(schema.names())}"
                )

    @property
    def _engine(self) -> Engine:
        return self.child._engine

    def _schema(self) -> Schema:
        # Output schema is built from scratch: group-by keys (preserving
        # their original domains from the child) followed by aggregate output
        # columns (domains inferred by each AggSpec.output_domain). This is
        # not a projection of the child schema — it's a new schema entirely.
        from .schema import Attribute
        child_schema = self.child._schema()
        attrs = [child_schema[k] for k in self.keys]
        for name, agg in self.aggs.items():
            domain = agg.output_domain(child_schema)
            attrs.append(Attribute(name, domain))
        return Schema(tuple(attrs))

    @property
    def relation_name(self) -> str:
        return self.child.relation_name


# ---------------------------------------------------------------------------
# Phase 6: Division
# ---------------------------------------------------------------------------


class Division(BaseRelation):
    """÷ (Division): Tuples associated with ALL tuples in the divisor."""

    def __init__(self, left: BaseRelation, right: BaseRelation):
        self.left = left
        self.right = right
        _check_same_engine(left, right)
        # Right schema attrs must be a subset of left schema attrs
        left_names = set(left._schema().names())
        right_names = set(right._schema().names())
        # Divisor attributes must be a strict subset of dividend attributes.
        if not right_names.issubset(left_names):
            raise SchemaError(
                f"DIVISION requires the divisor's attributes to be a subset "
                f"of the dividend's attributes.\n\n"
                f"  Dividend: {left._schema()}\n"
                f"  Divisor:  {right._schema()}\n"
                f"  Not in dividend: {right_names - left_names}"
            )
        # Equal attribute sets would produce an empty result schema, which is
        # meaningless. Require strictly fewer attributes in the divisor.
        if right_names == left_names:
            raise SchemaError(
                f"DIVISION requires the divisor to have strictly fewer attributes "
                f"than the dividend. Both have: {left._schema()}"
            )

    @property
    def _engine(self) -> Engine:
        return self.left._engine

    def _schema(self) -> Schema:
        return self.left._schema().subtract(self.right._schema())

    @property
    def relation_name(self) -> str:
        return f"({self.left.relation_name} ÷ {self.right.relation_name})"
