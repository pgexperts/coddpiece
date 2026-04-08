# CLAUDE.md — coddpiece

A Python relational algebra teaching library backed by real databases via DB-API 2.0.

## Architecture

```
coddpiece/
    schema.py       # Attribute, Schema — strong typing, compatibility checks
    predicates.py   # Attr proxy, Predicate tree — captures Python expressions
    relation.py     # BaseRelation ABC, Relation leaf, all 16 expression nodes
    compiler.py     # Expression tree → SQL visitor (parameterized, clean output)
    engine.py       # DB-API 2.0 adapter, Dialect sniffing, table creation
    aggregates.py   # AggSpec, count/sum_/avg/min_/max_
    display.py      # Algebra notation, tree, table, gloss, explain renderers
    datasets.py     # Built-in suppliers-and-parts + employees datasets
    errors.py       # Educational error hierarchy with hints
```

## Key design patterns

- **Expression tree**: Every operation returns a new `BaseRelation` node (immutable).
  Method chaining builds a tree; nothing executes until `.collect()`.
- **Attr proxy**: `relation.column` returns an `Attr` object that captures comparisons
  as `Predicate` nodes instead of evaluating them. Uses `__eq__`, `__gt__`, etc.
  The `__bool__` trap catches accidental `and`/`or` usage with a helpful error.
- **Visitor compiler**: `Compiler._visit()` dispatches to `_compile_{nodetype}()`.
  Base relations emit bare table names; the `_as_source()` helper avoids
  unnecessary subqueries. Projection flattens Projection(Selection(Relation))
  into a single SELECT.
- **Set semantics**: All queries use DISTINCT by default. `.bags()` wraps the
  expression to strip DISTINCT for teaching the set/bag distinction.

## Running tests

```bash
python -m pytest tests/ -v
```

All tests use SQLite in-memory. Zero external dependencies.

## Common modifications

**Adding a new operation**: 
1. Add node class in `relation.py` inheriting `BaseRelation`
2. Add `_compile_{name}` method in `compiler.py`
3. Add algebra renderer case in `display.py` `render_algebra()`
4. Add tree label in `display.py` `_node_label()`
5. Add gloss template in `display.py` `_gloss_walk()`
6. Add children in `display.py` `_get_children()`
7. Add method on `BaseRelation` in `relation.py`
8. Add tests

**Improving SQL output**: The compiler's `_as_source()` method is the key
optimization point. It returns bare table names for `Relation` nodes and
subqueries for complex expressions. The `_compile_projection()` method
demonstrates chain-flattening (merging Projection+Selection into one query).
Extend this pattern for other common chains.

**Adding a new DB backend**: Extend `Dialect` in `engine.py` with detection
logic for the module name. Override `placeholder()`, `quote_identifier()`,
and `_introspect_schema()` as needed.

## Invariants

- Every `BaseRelation` subclass must implement `_engine`, `_schema()`, `relation_name`
- Schema operations validate eagerly at construction (not at execution)
- All SQL uses parameterized queries — literals never interpolated
- The compiler never calls `_visit()` on the same node twice in one compile
  (except Division, which needs the dividend twice for the correlation)
  
## Commenting standards

Comments should explain choices, tradeoffs, higher-level algorithms, constraints, and invariants — not restate what the code does. Each file should have a brief header noting its role in the overall system. Emphasize non-obvious side effects, ordering dependencies, and intentional design decisions. The audience is a reader (human or AI) encountering this code for the first time.

