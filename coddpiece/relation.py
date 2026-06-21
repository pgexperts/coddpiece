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

from .aggregates import AggSpec
from .errors import AttributeError_, DomainError, EngineError, SchemaError
from .predicates import Attr, PredicateNode
from .schema import Schema

if TYPE_CHECKING:
    from .engine import Engine


class BaseRelation(ABC):
    """Abstract base for all relation-valued expressions.

    This is the heart of the package. Every operation returns a new
    BaseRelation, enabling method chaining and lazy evaluation.

    Subclass contract (enforced by @abstractmethod):
      - _engine:       the Engine that will run this node; interior nodes
                       delegate to a child, leaves own it directly.
      - _schema():     compute the output schema. Must be side-effect-free
                       and cheap enough to call during tree construction,
                       because __post_init__ validation and the Attr proxy
                       both invoke it repeatedly.
      - relation_name: a short string used by display/error messages only.

    Lifecycle: nodes are constructed eagerly (validating schemas and engine
    identity on the spot) but execute nothing until .collect() / __iter__ /
    .count() is called. This is the core "trees are cheap, trips to the DB
    are not" contract the rest of the package relies on.
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

    # Extended operations: theta join, equijoin, and the asymmetric and
    # outer-join family. Schema rules differ from natural join — see the
    # individual node classes for the per-operator constraints.
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

    def group(self, *keys: str, **aggs: AggSpec) -> Grouping:
        """γ (Grouping/Aggregation): Group by keys, compute aggregates."""
        return Grouping(self, keys, aggs)

    # --- Materialization ---
    #
    # These are the ONLY methods on BaseRelation that actually hit the
    # database. Construction, chaining, schema inspection, algebra(), sql(),
    # tree(), and explain() are all pure — they build or read the tree.
    # Anything that touches real rows goes through .collect() (or the bag
    # wrapper's equivalent), which in turn asks the engine to compile and
    # execute the expression.

    def collect(self) -> list[tuple[Any, ...]]:
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

    def collect(self) -> list[tuple[Any, ...]]:
        from contextlib import closing

        from .compiler import Compiler
        # bag_mode=True threads through every compile method: SELECTs lose
        # their DISTINCT, and set operators (UNION/INTERSECT/EXCEPT) become
        # UNION ALL / INTERSECT ALL / EXCEPT ALL. This is the difference
        # between an honest bag-mode contrast and an illusion: SQL's bare
        # set operators dedupe even when the per-side SELECT does not, so
        # post-hoc string replacement on the SQL would silently re-impose
        # set semantics at every set operator.
        compiler = Compiler(self._expr._engine.dialect, bag_mode=True)
        sql, params = compiler.compile(self._expr)
        formatted = self._expr._engine.dialect.format_params(params)
        with closing(self._expr._engine.connection.cursor()) as cursor:
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
    """A base relation backed by a real database table.

    The sole LEAF type in the expression tree. Every other node in this
    module is an interior node whose engine/schema is derived from its
    children; a Relation is where that derivation bottoms out.

    Not a frozen dataclass because BaseRelation declares `_engine` as a
    property and `_schema` as a method; using a dataclass with those
    same names as fields would shadow them. The hand-written __init__
    sidesteps that with distinct backing-attribute names below.
    """

    def __init__(self, engine: Engine, table_name: str, schema: Schema):
        # Backing fields use single-underscore "internal" naming, not the
        # double-underscore mangling that the original code mixed
        # inconsistently across these three slots. The names are chosen
        # to NOT collide with `_engine` (BaseRelation property) or
        # `_schema` (BaseRelation method); the property/method below
        # delegate to these slots.
        self._owning_engine = engine
        self._table_name = table_name
        self._stored_schema = schema

    @property
    def _engine(self) -> Engine:
        return self._owning_engine

    def _schema(self) -> Schema:
        return self._stored_schema

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
    """σ (Selection): Filter rows by a predicate.

    Schema is pass-through: selection never changes the set of columns,
    only the set of rows. The predicate was already validated against the
    schema when the Attr proxy built it, so no __post_init__ check here.
    """

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
    """π (Projection): Keep only specified attributes.

    Set-semantic: duplicates introduced by dropping columns are eliminated
    by the compiler's DISTINCT. The compiler also recognizes the common
    Projection(Selection(Relation)) chain and emits a single SELECT for it.
    """

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
    """ρ (Rename): Rename attributes. mapping is {new_name: old_name}.

    The {new: old} direction (rather than {old: new}) is deliberate so the
    kwargs call site reads like variable assignment: .rename(n='name').
    """

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

    Eager schema-compatibility validation is a teaching choice: UNION /
    INTERSECT / EXCEPT in SQL will happily silently succeed on
    position-matching columns with mismatched meaning. Here we raise at
    construction so students see the error at the source line they wrote.
    Not a frozen dataclass: the op_name parameter differs per subclass,
    so we use ordinary __init__ instead of field defaults.
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
    """∪ (Union): All tuples in either relation.

    Set-semantic: duplicates across the two inputs collapse. The compiler
    emits plain UNION (not UNION ALL) to preserve this.
    """

    def __init__(self, left: BaseRelation, right: BaseRelation):
        super().__init__(left, right, "UNION")


class Intersect(_SetOp):
    """∩ (Intersect): Tuples in both relations.

    Commutative in theory; we still keep left/right ordering to preserve
    the display tree's left-to-right reading for students.
    """

    def __init__(self, left: BaseRelation, right: BaseRelation):
        super().__init__(left, right, "INTERSECT")


class Difference(_SetOp):
    """− (Difference): Tuples in left but not in right.

    Non-commutative; left/right order is load-bearing.
    """

    def __init__(self, left: BaseRelation, right: BaseRelation):
        # Relational algebra calls it "difference"; SQL calls it EXCEPT.
        super().__init__(left, right, "EXCEPT")


# ---------------------------------------------------------------------------
# Phase 3: CrossProduct, NaturalJoin, ThetaJoin, Equijoin
# ---------------------------------------------------------------------------


@dataclass(frozen=True, repr=False)
class CrossProduct(BaseRelation):
    """× (Cross Product): Cartesian product.

    Requires disjoint attribute names across the two relations — the
    result schema has no notion of "left.x vs right.x". Use Rename first
    if there are collisions.
    """

    left: BaseRelation
    right: BaseRelation

    def __post_init__(self):
        _check_same_engine(self.left, self.right)
        # Validate no name collisions — compose() raises SchemaError on
        # any overlap, satisfying the eager-validation invariant.
        self.left._schema().compose(self.right._schema())

    @property
    def _engine(self) -> Engine:
        return self.left._engine

    def _schema(self) -> Schema:
        return self.left._schema().compose(self.right._schema())

    @property
    def relation_name(self) -> str:
        return f"({self.left.relation_name} × {self.right.relation_name})"


@dataclass(frozen=True, repr=False)
class NaturalJoin(BaseRelation):
    """⋈ (Natural Join): Join on common attributes.

    Uses join_compose (not compose) for the result schema: common
    attributes appear once in the output, not twice.
    """

    left: BaseRelation
    right: BaseRelation

    def __post_init__(self):
        _check_same_engine(self.left, self.right)
        common = self.left._schema().common(self.right._schema())
        # Guardrail: a natural join with no common attributes silently becomes
        # a cross product, which is almost certainly a mistake. We force the
        # user to be explicit by using equijoin or rename instead.
        if len(common) == 0:
            raise SchemaError(
                f"NATURAL JOIN between {self.left.relation_name!r} and "
                f"{self.right.relation_name!r} found no common attributes.\n\n"
                f"  Left:  {self.left._schema()}\n"
                f"  Right: {self.right._schema()}\n\n"
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


@dataclass(frozen=True, repr=False)
class ThetaJoin(BaseRelation):
    """⋈θ (Theta Join): Join with an arbitrary predicate.

    Defined classically as σ_θ(R × S), so the schema rule matches
    CrossProduct: names across the two sides must be disjoint. The
    predicate is NOT validated against the composed schema here — the
    Attr proxy already validated each side when it was built.
    """

    left: BaseRelation
    right: BaseRelation
    predicate: PredicateNode

    def __post_init__(self):
        _check_same_engine(self.left, self.right)
        # Schema is full compose (must have disjoint names).
        self.left._schema().compose(self.right._schema())

    @property
    def _engine(self) -> Engine:
        return self.left._engine

    def _schema(self) -> Schema:
        return self.left._schema().compose(self.right._schema())

    @property
    def relation_name(self) -> str:
        return f"({self.left.relation_name} ⋈θ {self.right.relation_name})"


@dataclass(frozen=True, repr=False)
class Equijoin(BaseRelation):
    """⋈= (Equijoin): Join where left_attr = right_attr.

    Sits between ThetaJoin (fully general, full compose schema) and
    NaturalJoin (implicit, all common attrs). Here the join attribute
    names can differ, but the right-side join column is dropped from the
    output so the result has no redundant column.
    """

    left: BaseRelation
    right: BaseRelation
    left_attr: str
    right_attr: str

    def __post_init__(self):
        _check_same_engine(self.left, self.right)
        if self.left_attr not in self.left._schema():
            raise AttributeError_(
                f"Left relation has no attribute {self.left_attr!r}. "
                f"Available: {', '.join(self.left._schema().names())}"
            )
        if self.right_attr not in self.right._schema():
            raise AttributeError_(
                f"Right relation has no attribute {self.right_attr!r}. "
                f"Available: {', '.join(self.right._schema().names())}"
            )

    @property
    def _engine(self) -> Engine:
        return self.left._engine

    def _schema(self) -> Schema:
        # Unlike cross product, equijoin drops the duplicate join column from
        # the right side (since left_attr = right_attr, keeping both is
        # redundant). Then we check for remaining name collisions.
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


@dataclass(frozen=True, repr=False)
class Semijoin(BaseRelation):
    """⋉ (Semijoin): Left tuples that have a match in right.

    Output schema is the LEFT schema only — the right relation acts as a
    filter. Guarded to require at least one common attribute, since a
    semijoin with no join condition would be degenerate.
    """

    left: BaseRelation
    right: BaseRelation

    def __post_init__(self):
        _check_same_engine(self.left, self.right)
        common = self.left._schema().common(self.right._schema())
        if len(common) == 0:
            raise SchemaError(
                f"SEMIJOIN requires common attributes. "
                f"None found between {self.left.relation_name!r} "
                f"and {self.right.relation_name!r}."
            )

    @property
    def _engine(self) -> Engine:
        return self.left._engine

    def _schema(self) -> Schema:
        return self.left._schema()

    @property
    def relation_name(self) -> str:
        return f"({self.left.relation_name} ⋉ {self.right.relation_name})"


@dataclass(frozen=True, repr=False)
class Antijoin(BaseRelation):
    """▷ (Antijoin): Left tuples with NO match in right.

    Dual of Semijoin: same schema rule (left-only), same common-attribute
    requirement. Compiled as NOT EXISTS / EXCEPT rather than LEFT JOIN
    WHERE NULL so NULLs in the right side don't produce false matches.
    """

    left: BaseRelation
    right: BaseRelation

    def __post_init__(self):
        _check_same_engine(self.left, self.right)
        common = self.left._schema().common(self.right._schema())
        if len(common) == 0:
            raise SchemaError(
                f"ANTIJOIN requires common attributes. "
                f"None found between {self.left.relation_name!r} "
                f"and {self.right.relation_name!r}."
            )

    @property
    def _engine(self) -> Engine:
        return self.left._engine

    def _schema(self) -> Schema:
        return self.left._schema()

    @property
    def relation_name(self) -> str:
        return f"({self.left.relation_name} ▷ {self.right.relation_name})"


@dataclass(frozen=True, repr=False)
class OuterJoin(BaseRelation):
    """⟕⟖⟗ (Outer Join): Join preserving unmatched tuples.

    `how` is validated against VALID_HOW at construction to catch typos
    before execution. Only three directions are meaningful; we validate
    explicitly rather than trusting the DB to reject bad SQL later.

    VALID_HOW is a class-level constant (no annotation), so dataclass
    leaves it alone — only the annotated `left`, `right`, `how` slots
    become dataclass fields.
    """

    VALID_HOW = ("left", "right", "full")

    left: BaseRelation
    right: BaseRelation
    how: str = "full"

    def __post_init__(self):
        if self.how not in self.VALID_HOW:
            raise ValueError(
                f"'how' must be one of {self.VALID_HOW}, got {self.how!r}"
            )
        _check_same_engine(self.left, self.right)
        common = self.left._schema().common(self.right._schema())
        if len(common) == 0:
            raise SchemaError(
                f"OUTER JOIN requires common attributes. "
                f"None found between {self.left.relation_name!r} "
                f"and {self.right.relation_name!r}."
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


@dataclass(frozen=True, repr=False)
class Grouping(BaseRelation):
    """γ (Grouping/Aggregation).

    Unlike most nodes, Grouping's output schema is not a function of the
    child's columns alone — it's (group keys) ++ (aggregate output
    columns), with aggregate domains inferred via AggSpec.output_domain.
    Validated eagerly: unknown group keys fail at construction.
    """

    child: BaseRelation
    keys: tuple[str, ...]
    aggs: dict[str, AggSpec]

    def __post_init__(self):
        # Validate group keys exist on the child schema. This is the
        # earliest point we can catch typos like .group("snO", ...) — the
        # alternative would be a SQL error at .collect() time, far from
        # the offending source line.
        schema = self.child._schema()
        for k in self.keys:
            if k not in schema:
                raise AttributeError_(
                    f"Grouping key {k!r} not in schema. "
                    f"Available: {', '.join(schema.names())}"
                )
        # Validate aggregate target attributes the same way. Skip "*"
        # (COUNT(*) has no column). Without this check an unknown attr is
        # emitted as a quoted identifier, which SQLite silently reads as a
        # string literal — SUM/AVG of it returns 0.0 with no error — so the
        # bug surfaces as wrong numbers rather than a diagnostic, far from
        # the offending source line.
        for agg in self.aggs.values():
            if agg.attr != "*" and agg.attr not in schema:
                raise AttributeError_(
                    f"Aggregate attribute {agg.attr!r} not in schema. "
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


@dataclass(frozen=True, repr=False)
class Division(BaseRelation):
    """÷ (Division): Tuples associated with ALL tuples in the divisor.

    The one deliberate exception to the compiler's "visit each node
    once" invariant: the SQL form of R ÷ S correlates R against itself
    (roughly: R − π_{R−S}((π_{R−S}(R) × S) − R)), so the dividend
    subtree is emitted twice. See compiler._compile_division for the
    mechanics.

    Schema constraints (validated here, not at compile time):
      - divisor's attributes must be a SUBSET of dividend's
      - divisor must have STRICTLY fewer attributes; equal sets would
        leave a zero-attribute result schema, which is meaningless.
    """

    left: BaseRelation
    right: BaseRelation

    def __post_init__(self):
        _check_same_engine(self.left, self.right)
        left_names = set(self.left._schema().names())
        right_names = set(self.right._schema().names())
        # Divisor attributes must be a strict subset of dividend attributes.
        if not right_names.issubset(left_names):
            raise SchemaError(
                f"DIVISION requires the divisor's attributes to be a subset "
                f"of the dividend's attributes.\n\n"
                f"  Dividend: {self.left._schema()}\n"
                f"  Divisor:  {self.right._schema()}\n"
                f"  Not in dividend: {right_names - left_names}"
            )
        # Equal attribute sets would produce an empty result schema, which is
        # meaningless. Require strictly fewer attributes in the divisor.
        if right_names == left_names:
            raise SchemaError(
                f"DIVISION requires the divisor to have strictly fewer attributes "
                f"than the dividend. Both have: {self.left._schema()}"
            )
        # Shared-attribute domains must match — same rule Schema.common
        # enforces for natural join. Without this, the inner EXCEPT inside
        # the compiled NOT EXISTS would compare values across different
        # types (e.g. str vs int), which most backends silently coerce in
        # surprising ways. Validating here keeps the error close to the
        # offending construction site, matching the eager-validation
        # invariant the rest of this module follows.
        left_schema = self.left._schema()
        right_schema = self.right._schema()
        for name in right_schema.names():
            ldom = left_schema[name].domain
            rdom = right_schema[name].domain
            if ldom != rdom:
                raise DomainError(
                    f"DIVISION requires matching domains for shared attributes.\n\n"
                    f"  Dividend: {left_schema}\n"
                    f"  Divisor:  {right_schema}\n"
                    f"  Mismatch on {name!r}: "
                    f"{ldom.__name__} (dividend) vs {rdom.__name__} (divisor).\n\n"
                    f"  Hint: Use RENAME or PROJECT to align types before DIVIDE."
                )

    @property
    def _engine(self) -> Engine:
        return self.left._engine

    def _schema(self) -> Schema:
        return self.left._schema().subtract(self.right._schema())

    @property
    def relation_name(self) -> str:
        return f"({self.left.relation_name} ÷ {self.right.relation_name})"
