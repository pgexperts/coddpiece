# Theory of Operation

This document is for an engineer picking up coddpiece to change it. It explains
*why* the code is shaped the way it is — the ideas, the tradeoffs, and the
decisions that the code records only the outcome of. For the enumerated module
map and the authoritative invariant list, see [ARCHITECTURE.md](ARCHITECTURE.md);
this document points at those rather than copying them, and spends its words on
the reasoning behind them. For install and usage, and for an introduction to
relational algebra as a subject, see [README.md](README.md).

## Orientation

coddpiece exists to make one equivalence visible: a relational-algebra
expression and the SQL it corresponds to are *the same query wearing different
clothes*. A student writes `s.select(s.city == "London").project("sname")`,
and the library shows them the algebra (`π(sname)(σ(city="London")(s))`), the
SQL, and the result — all from the same object. The pedagogical bet is that
seeing the two notations side by side, repeatedly, is what makes SQL stop
feeling like a pile of special cases.

Everything in the design serves that bet, and it produces one overriding
constraint: **what the library displays as algebra and what it executes as SQL
must never disagree.** A divergence isn't a cosmetic bug; it teaches the student
something false. If you internalize only one thing before changing this code,
make it that — most of the non-obvious decisions below are downstream of it.

Mechanically, the shape is a small, conventional pipeline: a **lazy, immutable
expression tree** built by method chaining, a **single-pass compiler** that
turns the tree into parameterized SQL, and a thin **DB-API 2.0 engine** that
runs it against whatever real database you hand it (SQLite, PostgreSQL, MySQL).
Nothing executes until you ask for rows. The cleverness is concentrated in two
places — how predicates are captured, and how the tree decides equality — and
both are explained under Core ideas.

## How it's structured

The component boundaries (enumerated in ARCHITECTURE.md) fall where they do for
reasons worth knowing:

- **`relation.py` is the spine.** `BaseRelation` holds the entire chaining API
  (`select`, `project`, `join`, …) so that *every* node — leaf or interior —
  answers the same method surface; this is what makes operations "compose
  freely, just like arithmetic" (the closure property the README leans on). The
  tree has exactly one **leaf**, `Relation`, backed by a real table. Every other
  node is an **interior** node that derives its engine and schema from its
  children. That single-leaf shape is why schema validation can bottom out
  cleanly: derivation always terminates at a table whose schema is known.

- **`predicates.py` is a separate tree** from the relation tree, deliberately.
  Predicates (the `WHERE`-clause world) compose by different rules than
  relations (the `FROM`-clause world), and keeping them apart keeps each small.
  The bridge between them is the `Attr` proxy — the single most load-bearing
  trick in the codebase.

- **`compiler.py` is the only place that knows SQL syntax**, and `engine.py`'s
  `Dialect` is the only place that knows *backend* syntax. This two-level split
  (SQL structure vs. dialect quirks) is what lets one compiler target three
  databases: the compiler asks the `Dialect` for a placeholder or a quoted
  identifier and never writes either itself.

- **`schema.py` is the currency of validation.** A `Schema` is an ordered
  attribute→type map, and every node can produce one. Because nodes validate at
  construction (see Core ideas), `Schema` is consulted constantly, on tiny
  inputs — which is why its linear scans are left unoptimized on purpose.

## Core ideas

### 1. The `Attr` proxy: hijacking Python's operators

`s.city == "London"` does not compare anything. `Attr.__eq__` returns a
`Predicate` node. This is the trick the whole predicate DSL rests on: comparison
operators are overloaded to *capture* an expression tree instead of *evaluating*
a boolean. The same expression then renders as algebra, compiles to SQL, and
explains itself.

The cost is a **deliberate Liskov violation** — `object.__eq__` is contracted to
return `bool`, and `Attr` returns `Predicate`. This is acknowledged in the code
(`# type: ignore[override]` on `__eq__`/`__ne__`) and is the reason the package
does not turn on mypy `strict` wholesale (see `pyproject.toml`'s type posture
comment). It also forces three secondary mechanics, each of which exists to make
the violation safe rather than merely tolerated:

- **`__bool__` raises `PredicateError`.** Python routes the `and`/`or`/`not`
  keywords through `__bool__`, and those keywords *cannot* be overloaded. Rather
  than let `x == 1 and y == 2` silently evaluate `bool(x == 1)` to something
  meaningless, `Attr.__bool__` intercepts it and tells the user to write
  `&`/`|`/`~`. The error message is part of the teaching surface, not an
  afterthought.
- **`__hash__` is hand-written over `(id(self.source), name)`.** Since `__eq__`
  no longer returns `bool`, the default hash is meaningless; identity-based
  hashing is both correct (the tree wants identity — see idea 2) and necessary.
- **The ordering operators carry no `type: ignore`.** `object` doesn't define
  `__lt__`/`__le__`/`__gt__`/`__ge__`, so those are fresh definitions, not
  overrides — and with `warn_unused_ignores` on, a stray ignore there would be a
  CI failure. This asymmetry between `==`/`!=` and `<`/`>` is easy to "tidy" into
  a bug.

### 2. Identity, not value, is the tree's notion of equality

Two expression nodes are equal iff they are the *same object*. The frozen node
dataclasses pass `eq=False`, falling back to `object` identity for both `__eq__`
and `__hash__`. There are two independent reasons, and you need both to
understand why this isn't negotiable:

- **Value equality is impossible here.** A structural `__eq__` (the dataclass
  default) compares fields tuple-wise, which reaches an `Attr`, which returns a
  `Predicate`, on which `bool()` raises. So `node == node`, `node in [...]`, and
  `set(nodes)` would *all* raise `PredicateError` — and dict-bearing nodes
  (`Grouping`, `Rename`) would additionally be unhashable. (See ISSUES A1 for
  the full failure analysis.)
- **The tree *wants* identity anyway.** The engine-equivalence check uses `is`,
  and `Attr` hashes on `id(self.source)`. The relational model treats relations
  by value, but the *expression objects* modeling them are most usefully treated
  by identity.

There is a genuine open question parked here: a future optimizer that dedupes
subtrees would need *structural* equality of trees, which `==` can't provide
while predicates own `__eq__`. The decision was to take the clean `eq=False`
fix now and reach for an explicit structural key if and when that feature
arrives (the reversal is harder later). See the open question in
`docs/reviews/review-20260621-080500.md`.

### 3. Eager validation

Every node validates its own schema and attribute references *at construction*,
not when the SQL is compiled or run. `s.project("typo")` raises immediately,
pointing at the `project` call — not three chained operations later. In a
teaching tool, where the user is learning what is and isn't a legal operation,
the error has to land on the offending step or it teaches nothing.

This invariant is asserted in module headers, in `schema.py`, and in the test
suite's phase banners — which is what made two *holes* in it visible and worth
fixing: aggregate-attribute existence and `Equijoin` output-collision were
originally deferred to `_schema()` (ISSUES C2, A2). They're now closed, so the
documented guarantee holds literally. The lesson for a maintainer: a new node's
`__post_init__` must validate everything the node could get wrong, because the
whole suite of sibling nodes — and the docs — promise it does.

### 4. The Dialect: generic where it can be, hard-coded where it must be

`Dialect` adapts to a DB-API connection by sniffing the driver. The split in how
it does so is the interesting part:

- **Paramstyle is discovered generically**, by walking the driver module
  hierarchy for PEP 249's `paramstyle` attribute. So most drivers "just work"
  for parameter binding with no coddpiece change.
- **Identifier quoting is hard-coded per driver** (`psycopg`/`pg8000` → `"`,
  `mysql`/`pymysql` → `` ` ``, default `"`), because PEP 249 offers *no*
  standard for quoting. This is the explicit extension point: a new backend
  whose quoting differs from ANSI double-quotes needs a branch.
- **`setop_all_support` is reported per dialect**, so `.bags()` over
  `INTERSECT`/`EXCEPT` can raise a clear `NotImplementedError` on SQLite
  (which never implemented `INTERSECT ALL`/`EXCEPT ALL`) instead of surfacing a
  driver-level "syntax error near ALL".

`Dialect.placeholder()` is described in its own docstring as "the single choke
point enforcing the no-literal-interpolation invariant" — the compiler asks it
for the *token* to splice in, and the real value travels separately in
`Compiler.params`. That choke point is why the security posture is a one-line
claim rather than an audit (see Invariants below).

### 5. Set semantics by default

Pure relational algebra is set-based: no duplicate rows, ever. SQL defaults the
other way (bag semantics). coddpiece sides with the algebra — every compiled
query is `DISTINCT` — "to keep the algebra honest" (README). `.bags()` is the
explicit, threaded opt-out (a real `bag_mode` flag through the compiler, not a
post-filter), so a student can *see* the difference between
`sp.project("sno")` (4 rows) and `sp.project("sno").bags()` (12) rather than
take it on faith.

### 6. Division is the payoff

Relational division — "find X associated with *all* Y" — has no SQL keyword and
compiles to the notoriously opaque `NOT EXISTS … EXCEPT … NOT EXISTS`
double-negation. The library renders the algebra (`÷`) next to that SQL so the
pattern clicks. This is why the compiler is careful to parenthesize division's
inner `EXCEPT` — and, by the same token, why the chained set-op bug (ISSUES C1)
was a real problem and not a quibble: the author already knew parentheses were
load-bearing for `EXCEPT`; one code path just hadn't applied the knowledge.

## Design decisions and tradeoffs

The decision *record* is `ISSUES.md` (resolved findings) and its "Won't fix / by
design" section; this section synthesizes the load-bearing ones and cites the
entry rather than reproducing the log, which is maintained and will keep moving.

- **`OuterJoin` defaults to `how="full"`** (ISSUES W1). `full` carries the most
  information, so it's a defensible default; the real reason it stays is that
  changing it would be a breaking API change at 1.0. A maintainer might still
  consider making `how` *required* for explicitness in a teaching API — the
  decision was to keep the default, not that requiring it would be wrong.

- **`__getattr__` raises a bare `AttributeError(name)` for `_`-prefixed names**
  (ISSUES W2). This is the hot internal-attribute-lookup path; the terseness is
  intentional. The tradeoff accepted: a genuinely-missing dunder surfaces as a
  context-free error. Kept because the path's speed matters more than the
  message for names no user types.

- **PG introspection hardcodes `%s`** (ISSUES W3) instead of routing through
  `Dialect`. A documented reliance on psycopg's paramstyle tolerance; the
  decision is to revisit only if a strict-paramstyle PG driver appears. It is
  the one live exception to "all SQL flows through the Dialect," and it is
  flagged as such.

- **Per-row inserts in `Engine`** are a documented pedagogical choice, not an
  oversight — simplicity and readability over bulk-insert throughput, justified
  by the library's teaching framing.

- **Conventions live in `ARCHITECTURE.md`, not an in-tree `CLAUDE.md`.** An
  earlier in-tree conventions doc was removed and its references inlined (commit
  `f25667b`); project memorialization belongs in the architecture doc.

> **Why "backed by real databases" rather than an in-memory evaluator?**
> Executing against an actual DB-API connection is what makes the algebra⇔SQL
> equivalence *demonstrable* rather than asserted — the student runs the SQL the
> library shows them. The deeper rationale for choosing real-DB execution over a
> self-contained interpreter is not separately recorded in project sources.
> > Rationale not fully recovered from project sources; the above is inferred
> > from the library's stated teaching goal, not from an explicit decision note.

## Invariants, and why they hold

The invariants are enumerated authoritatively in
[ARCHITECTURE.md](ARCHITECTURE.md#invariants). The "why" for the subtle ones is
already given above — they are not independent rules but consequences of the
core ideas:

- *Algebra ⇔ SQL fidelity* is the product premise (Orientation).
- *Identity equality* and *no-value-equality* both fall out of the `Attr` trick
  (Core idea 2).
- *All SQL through `Dialect`* and *literals never interpolated* are the same
  choke point (Core idea 4); the security claim is small precisely because every
  value goes through `params` and every identifier through `quote_identifier`'s
  quote-doubling, leaving only structural keywords and aliases as string-built
  SQL.
- *Set semantics by default* is the honesty-to-the-algebra choice (Core idea 5).
- *Set-op operands wrapped when nested* is division's parenthesization lesson
  generalized (Core idea 6 / ISSUES C1).

## Where the bodies are buried

- **The `type: ignore` asymmetry in `Attr`** (idea 1) is a tripwire: `==`/`!=`
  need the ignore, the ordering ops must not have it, and `warn_unused_ignores`
  turns a wrong guess into a CI failure.
- **`Relation` is hand-written, not a dataclass**, to avoid its fields shadowing
  `BaseRelation`'s `_engine` property and `_schema()` method. The backing-field
  names (`_owning_engine`, etc.) exist solely for that reason; an earlier version
  mixed single- and double-underscore mangling here inconsistently.
- **SQLite's "double-quoted unknown identifier becomes a string literal"
  misfeature** is why a typo'd aggregate attribute could silently return `0.0`
  instead of erroring (ISSUES C2). The fix is eager validation, but the misfeature
  is worth remembering whenever you build an identifier you didn't validate.
- **`format_sql`'s keyword list is ordered longest-first on purpose** — process
  `JOIN` before `LEFT OUTER JOIN` and you split the multi-word keyword across two
  lines (ISSUES E1). The ordering is the fix; don't "alphabetize" it.
- **The non-`qmark` paramstyle branches and the `%s` introspection shortcut are
  only as trustworthy as the Postgres CI job's assertions.** They have unit
  coverage for placeholder shape, but a live parameterized round-trip on PG/MySQL
  is the thing that would actually exercise them end to end (see "Backend
  coverage" in ARCHITECTURE).

## Making common changes

- **Add an algebra operation.** Add a node class to `relation.py`: frozen,
  `eq=False`, an eager `__post_init__` that validates against the child
  schema(s), and a `_schema()` that derives the output schema. Add the chaining
  method to `BaseRelation`. Add a `_compile_*` arm to the compiler that asks the
  `Dialect` for every placeholder and identifier (never write `?` or a quote
  yourself). If the node renders algebra notation, wire it into `display.py`. Add
  a row to the README operation table. The contract you must satisfy: the
  displayed algebra and the compiled SQL must denote the same query, including
  grouping — if your node can nest, make sure operands are wrapped (idea 6).

- **Add a database backend.** Paramstyle is auto-detected; you mostly need a
  quote-char branch and a `setop_all_support` branch in `Dialect`, plus a check
  that `Engine`'s introspection queries work on that backend. Then add a CI job
  that round-trips a *parameterized* query, because that is the only thing that
  truly validates the non-qmark path.

- **Add an aggregate.** Add an `AggSpec`/constructor in `aggregates.py`, handle
  it in `_compile_grouping`, and — per the eager-validation invariant — validate
  its attribute against the child schema in `Grouping.__post_init__`, not at
  compile time.

- **Tighten typing.** The package ships `py.typed`, so its annotations are a
  public contract (`disallow_any_generics` is already enforced to stop bare
  `list`/`dict`/`tuple` leaking). The blocker to full `strict` is the `Attr`
  Liskov violation; expect localized `# type: ignore[override]`, not a
  project-wide opt-out (`pyproject.toml` states this posture).
