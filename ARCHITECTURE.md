# Architecture

coddpiece is a relational-algebra teaching library. Method chaining builds an
**immutable expression tree**; a single-pass compiler translates that tree to
**DB-API 2.0 SQL** that runs only on demand. The one fact that governs every
design choice: **the rendered algebra notation and the executed SQL must denote
the same query** — fidelity between the two is the product, not a nicety.

## Component map

The authoritative module enumeration. THEORY.md refers here rather than
restating it.

| Component | Responsibility | Entry points |
|---|---|---|
| `coddpiece/relation.py` | Expression-tree nodes + the chaining API. `BaseRelation` (abstract base: `_engine` property, `_schema()` method, all chaining methods), `Relation` (the sole leaf, backed by a real table), interior nodes (`Selection`, `Projection`, `Rename`, `CrossProduct`, `NaturalJoin`, `ThetaJoin`, `Equijoin`, `Semijoin`, `Antijoin`, `OuterJoin`, `Union`/`Intersect`/`Difference` over `_SetOp`, `Division`, `Grouping`), `BagWrapper`. | `BaseRelation.select/project/join/…/collect/explain/bags`, `Relation` |
| `coddpiece/predicates.py` | Predicate tree + the `Attr` proxy that captures Python comparisons as nodes. | `Attr`, `Predicate`, `CompoundPredicate`, `NotPredicate`, `Literal` |
| `coddpiece/compiler.py` | Single-pass tree → SQL; accumulates bind params; threads set/bag mode. | `Compiler.compile`, `Compiler._visit` |
| `coddpiece/engine.py` | DB-API connection wrapper + `Dialect` (paramstyle, quoting, `setop_all_support` sniffing), table creation, schema introspection, execution. | `Engine`, `Engine.create`, `Dialect` |
| `coddpiece/schema.py` | `Schema` = ordered attribute→type map; the unit of eager validation. | `Schema` |
| `coddpiece/aggregates.py` | Aggregate specs and constructors. | `AggSpec`, `count`, `sum_`, `avg`, `min_`, `max_` |
| `coddpiece/display.py` | Box-drawing table rendering, algebra notation, SQL pretty-printer, `explain()`. | `render_table`, `format_sql`, `render_tree` |
| `coddpiece/errors.py` | Error hierarchy; `AttributeError_` multiply-inherits `AttributeError` so `hasattr`/`getattr` behave. | `RelationalError`, `SchemaError`, `AttributeError_`, `PredicateError` |
| `coddpiece/datasets.py` | The suppliers-and-parts teaching dataset (C.J. Date). | `suppliers_and_parts` |
| `coddpiece/__init__.py` | Public surface. | `Engine`, the aggregate constructors |

## Invariants

The load-bearing rules. THEORY.md explains *why* each holds; here is the rule
and what breaks without it.

- **Algebra display and compiled SQL denote the same query.** Breaks: the entire teaching premise. A tree whose grouping the SQL flattens is a correctness bug, not cosmetics.
- **Validation is eager: every node checks its schema/attributes at construction (`__post_init__`, or `__init__` for `Relation`).** Breaks: errors surface late at `_schema()`/execute instead of at the offending call, and `.schema()` and `.collect()` can disagree on the same node. Asserted in `schema.py`, `relation.py`, and the test-suite banners.
- **Expression-tree nodes use identity equality and identity hash (`eq=False` on the frozen dataclasses).** Breaks: the synthesized structural `__eq__` walks into `Attr.__eq__`, which returns a `Predicate`; `bool()` on it raises, so `==`, `in`, and `set()` all blow up, and dict-bearing nodes (`Grouping`, `Rename`) become unhashable.
- **All SQL text flows through `Dialect`: placeholders via `Dialect.placeholder()`, identifiers via `Dialect.quote_identifier()`.** Breaks: paramstyle/quoting wrong on any non-SQLite backend.
- **Literal values are never interpolated into SQL.** They accumulate in `Compiler.params` and bind through the driver's `execute()`. Breaks: SQL injection; the entire security posture rests here.
- **Set semantics by default: every compiled query is `DISTINCT` unless wrapped by `.bags()`.** Breaks: duplicate rows leak, contradicting the relational model the library teaches.
- **A set-op operand that is itself a set-op is parenthesized / subquery-wrapped when compiled.** Breaks: chained or mixed `UNION`/`INTERSECT`/`EXCEPT` regroups under backend precedence and returns wrong rows.

## Landmines

Non-obvious constraints a cold worker would get wrong.

- **`Attr` comparison operators return `Predicate`, not `bool` — a deliberate Liskov violation.** Build predicates with `&`/`|`/`~`; never `and`/`or`/`not` — those keywords invoke `Attr.__bool__`, which raises `PredicateError` by design. The `# type: ignore[override]` on `__eq__`/`__ne__` is load-bearing; the ordering ops (`__lt__` etc.) must **not** carry it (they are fresh defs, not overrides, and `warn_unused_ignores` will flag a stray one).
- **`@dataclass(frozen=True)` only synthesizes dunders you didn't write.** `Attr` hand-writes `__eq__`/`__hash__` and stays frozen; nodes pass `eq=False` to *suppress* the synthesized pair. Touching either decorator silently changes equality/hash semantics across the tree.
- **`Relation` is intentionally not a dataclass.** Its backing fields are named `_owning_engine` / `_table_name` / `_stored_schema` to avoid shadowing `BaseRelation`'s `_engine` property and `_schema()` method. Do not "dataclass-ify" it.
- **`OuterJoin` defaults to `how="full"`** — a bare `.outer_join(x)` is the widest, most expensive join. Deliberate; kept for 1.0 API stability.
- **`BaseRelation.__getattr__` raises a bare `AttributeError(name)` for `_`-prefixed names** — deliberate terseness on the hot internal-attribute-lookup path (the guard against recursing into `_schema()`/dunders). Accepted tradeoff: a genuinely-missing dunder surfaces as a context-free error, for names no user types.
- **`RIGHT`/`FULL OUTER JOIN` require SQLite ≥ 3.39 (2022).** `how="left"` works everywhere; the other two raise `OperationalError` on older bundled SQLite.
- **`INTERSECT ALL` / `EXCEPT ALL` are unsupported on SQLite.** `.bags()` over those ops raises a clear `NotImplementedError`, gated by `Dialect.setop_all_support`.
- **PG `information_schema` introspection hardcodes `%s`** and relies on psycopg's paramstyle tolerance — it is *not* routed through `Dialect`. A strict-paramstyle PostgreSQL driver would break it.
- **Backend coverage:** SQLite is exercised by every CI test. The PG/MySQL paramstyle, quoting, and introspection branches exist but are not round-tripped against a live database in CI — treat them as inference until the Postgres job asserts on a parameterized round-trip.

## Flow

**Build (no DB contact):** `engine.create(...)` or introspection yields a
`Relation` (leaf) → each chaining method wraps the current node in an interior
node, validating eagerly as it goes. The tree is immutable.

**Execute:** `.collect()` or `print()` → `Compiler.compile(node)` resets
`params`, walks the tree via `_visit` (dispatch on node type), asks `Dialect`
for placeholders and quoted identifiers, and emits one `SELECT … DISTINCT`
(or `… ALL` under bag mode) → `Engine` runs it through a `with closing(cursor)`
and returns tuples. `.explain()` renders algebra notation, the tree, the SQL,
and a plain-English reading side by side.

## Where to change X

- **Add an algebra operation:** new node class in `relation.py` (frozen, `eq=False`, eager `__post_init__` validation, a `_schema()` derivation) + a chaining method on `BaseRelation` + a `_compile_*` arm in `compiler.py` + a row in the README operation table.
- **Add a database backend:** paramstyle is auto-sniffed via PEP 249; add quote-char and `setop_all_support` branches to `Dialect` (`engine.py`) and verify `Engine` introspection.
- **Add an aggregate:** `aggregates.py` (`AggSpec` + constructor) and the `_compile_grouping` path; validate the aggregate attribute eagerly in `Grouping.__post_init__`.
- **Change SQL rendering:** `display.py` — `format_sql`'s keyword list is ordered longest-first on purpose (so `LEFT OUTER JOIN` matches before bare `JOIN`).
- **Record a design decision:** here (Landmines / Invariants) or [THEORY.md](THEORY.md); **track a defect or improvement:** GitHub issues.

---

For *why* any of this is the way it is, see [THEORY.md](THEORY.md). For
install, usage, and the relational-algebra primer, see [README.md](README.md).
