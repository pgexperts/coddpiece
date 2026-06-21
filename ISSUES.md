# Open Issues

Tracker for known defects and improvements, derived from the fresh-eyes review
in [docs/reviews/review-20260621-080500.md](docs/reviews/review-20260621-080500.md).
Issues resolved in the prior review cycle (commit `8c247ad`) have been dropped;
see that review for history. Items are grouped by category and ordered by
priority (P1 highest). Each is closed by a single PR per category.

Legend: `[ ]` open · `[x]` resolved · `[~]` won't fix / by design.

---

## P1 — Correctness (wrong results)

- [x] **C1 · Chained set operations are compiled without parentheses.**
  `Compiler._compile_setop` emits `left OP right` with both operands as bare
  `SELECT … OP …` strings, so a nested set-op tree is flattened. SQLite evaluates
  a flat compound left-to-right, so `a.difference(b.difference(c))` — algebra
  `(a − (b − c))` — runs as `(a − b) − c` and returns the wrong rows. Mixed
  `UNION`/`INTERSECT` is additionally wrong on standard backends (PostgreSQL),
  where `INTERSECT` binds tighter. The rendered algebra and the executed SQL
  disagree, which defeats the library's core teaching purpose.
  *Fix:* parenthesize (or subquery-wrap) any set-op operand that is itself a
  set-op. Regression test: three-relation `EXCEPT` nesting + mixed precedence.

- [x] **C2 · Invalid aggregate attribute silently returns garbage.**
  `Grouping.__post_init__` validates group keys but not the attributes inside
  `aggs`, and `_compile_grouping` emits `SUM("col")` without a schema check.
  `sp.group("sno", bad=sum_("nope")).collect()` returns `[('S1', 0.0), …]` on
  SQLite (unknown double-quoted identifier → string literal), with no error —
  while the same node's `.schema()` raises `AttributeError_`. Violates the
  eager-validation invariant and produces wrong data.
  *Fix:* validate each non-`"*"` aggregate attribute against the child schema in
  `Grouping.__post_init__`. Regression test: bad agg attr raises at construction.

## P2 — API design / node semantics

- [x] **A1 · Expression-tree nodes have value-equality enabled, and it raises.**
  The node dataclasses use the default `eq=True`. The generated `__eq__` compares
  fields tuple-wise, reaching `Attr.__eq__`, which returns a `Predicate`;
  `bool()` on it raises `PredicateError`. So `node == node`, `node in [...]`, and
  `set(nodes)` all blow up. `eq=True`+`frozen=True` also synthesizes `__hash__`
  over the fields, making `Grouping`/`Rename` (dict field) unhashable.
  *Fix:* `eq=False` on the node dataclasses (identity equality + identity hash —
  what the immutable tree actually wants). Regression test: `==`/`hash` behave.

- [x] **A2 · `Equijoin` defers its output-collision check to `_schema()`.**
  `Equijoin.__post_init__` validates only join-attribute existence; the
  ambiguous-output-name `SchemaError` surfaces only when `_schema()` is later
  evaluated, unlike every sibling node. Eager-validation invariant hole.
  *Fix:* run the collision check in `__post_init__`. Regression test:
  colliding non-join column raises at construction.

## P3 — Dead code & imports

- [x] **D1 · `Predicate.sql()` / `CompoundPredicate.sql()` / `NotPredicate.sql()`
  are dead and dialect-blind.** Nothing outside the predicate hierarchy calls
  them; the real path is `Compiler._compile_predicate`. They hardcode `?`
  placeholders and unqualified column names, contradicting the Dialect invariant
  and trapping any maintainer who calls `pred.sql()`.
  *Fix:* remove the three methods (and the now-unused `SQL_OPERATORS` import in
  them stays available for the compiler).

- [x] **D2 · Function-local import in the compiler.** `_compile_predicate` does
  `from .predicates import SQL_OPERATORS` though `predicates` is already imported
  at module top with no circular-import reason. *Fix:* hoist to the top import.

- [x] **D3 · `Literal.__repr__` does not escape embedded quotes.** Strings are
  wrapped with a hand-written `"…"`; a value containing `"` breaks the algebra
  notation. *Fix:* escape, or use `repr`-based quoting.

## P4 — Display & docs

- [x] **E1 · `format_sql` splits `LEFT/RIGHT/FULL OUTER JOIN` across two lines.**
  The keyword list processes bare `JOIN` before the multi-word forms, so the
  newline is inserted mid-keyword. Cosmetic but mangles a teaching artifact.
  *Fix:* order the keyword list longest-first (or special-case `… OUTER JOIN`).

- [x] **E2 · README overstates SQLite zero-setup for outer joins.** `RIGHT`/`FULL
  OUTER JOIN` require SQLite ≥ 3.39 (2022); older platform Pythons raise
  `OperationalError`. *Fix:* add a one-line version caveat near the outer-join
  docs / backend-coverage note.

## P5 — Typing

- [x] **T1 · Bare `list`/`tuple` generics in public signatures.**
  `Dialect.format_params(params: list)`, `Engine._insert_rows(rows: list[tuple])`,
  `Engine.create(rows: list[tuple] | None)`. A `py.typed` package leaks these.
  *Fix:* parameterize (`list[Any]`, `list[tuple[Any, ...]]`).

## P6 — CI / tooling

- [x] **I1 · No dependency-audit step.** Add a report-only `pip-audit` job
  (non-blocking, per the repo's perf/audit-is-report-only convention) so shipped
  dependencies are scanned without gating the pipeline on advisory noise.

## P7 — Tests

- [x] **TT1 · Non-`qmark` paramstyle matrix is untested.**
  `Dialect.placeholder()` and `format_params()` handle `numeric`/`named`/
  `format`/`pyformat`, but only `qmark` is exercised. *Fix:* unit tests pinning
  placeholder shape and the list-vs-dict `format_params` contract for each style.

---

## Won't fix / by design

- [~] **W1 · `OuterJoin` defaults `how="full"`.** Deliberate; `full` is the
  widest-information default and changing it would be a breaking API change at
  1.0. Kept.
- [~] **W2 · `__getattr__` raises bare `AttributeError(name)` for `_`-prefixed
  names.** Intentional terseness on the hot internal-lookup path. Kept.
- [~] **W3 · PG `information_schema` introspection hardcodes `%s`.** Documented
  reliance on psycopg's paramstyle tolerance; revisit only if a strict PG driver
  appears. Kept.
