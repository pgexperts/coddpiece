# coddpiece

[![CI](https://github.com/pgexperts/coddpiece/actions/workflows/ci.yml/badge.svg)](https://github.com/pgexperts/coddpiece/actions/workflows/ci.yml)

A Python relational algebra teaching library backed by real databases via DB-API 2.0.

**Learn relational algebra first. Then SQL makes sense.**

Named for [E.F. Codd](https://en.wikipedia.org/wiki/Edgar_F._Codd), who
invented the relational model in 1970.  The other thing you're thinking of
is spelled differently.

---

## Part I: Using coddpiece

### Installation

```bash
pip install coddpiece
```

Requires Python 3.10+ and any [DB-API 2.0](https://peps.python.org/pep-0249/)
connection. SQLite ships with Python, so you can start immediately with zero
additional dependencies.

### Setup

```python
import sqlite3
from coddpiece import Engine
from coddpiece.datasets import suppliers_and_parts

engine = Engine(sqlite3.connect(":memory:"))
s, p, sp = suppliers_and_parts(engine)
```

This loads the classic suppliers-and-parts dataset from C.J. Date's
*An Introduction to Database Systems*, the most widely used example
in relational algebra teaching. Three relations:

**s** — Suppliers:
```
┌─────┬───────┬────────┬────────┐
│ sno │ sname │ status │ city   │
├─────┼───────┼────────┼────────┤
│ S1  │ Smith │ 20     │ London │
│ S2  │ Jones │ 10     │ Paris  │
│ S3  │ Blake │ 30     │ Paris  │
│ S4  │ Clark │ 20     │ London │
│ S5  │ Adams │ 30     │ Athens │
└─────┴───────┴────────┴────────┘
```

**p** — Parts:
```
┌─────┬───────┬───────┬────────┬────────┐
│ pno │ pname │ color │ weight │ city   │
├─────┼───────┼───────┼────────┼────────┤
│ P1  │ Nut   │ Red   │ 12.0   │ London │
│ P2  │ Bolt  │ Green │ 17.0   │ Paris  │
│ P3  │ Screw │ Blue  │ 17.0   │ Oslo   │
│ P4  │ Screw │ Red   │ 14.0   │ London │
│ P5  │ Cam   │ Blue  │ 12.0   │ Paris  │
│ P6  │ Cog   │ Red   │ 19.0   │ London │
└─────┴───────┴───────┴────────┴────────┘
```

**sp** — Shipments (which supplier ships which part, and how many):
```
┌─────┬─────┬─────┐
│ sno │ pno │ qty │
├─────┼─────┼─────┤
│ S1  │ P1  │ 300 │
│ S1  │ P2  │ 200 │
│ S1  │ P3  │ 400 │
│ S1  │ P4  │ 200 │
│ S1  │ P5  │ 100 │
│ S1  │ P6  │ 100 │
│ S2  │ P1  │ 300 │
│ S2  │ P2  │ 400 │
│ S3  │ P2  │ 200 │
│ S4  │ P2  │ 200 │
│ S4  │ P4  │ 300 │
│ S4  │ P5  │ 400 │
└─────┴─────┴─────┘
```

You can also create your own relations:

```python
employees = engine.create(
    "employees",
    {"eid": int, "name": str, "department": str, "salary": int},
    rows=[
        (1, "Alice", "Engineering", 120000),
        (2, "Bob",   "Engineering", 110000),
        (3, "Carol", "Sales",       90000),
    ],
)
```

### Basic Operations

Everything is method chaining. Nothing touches the database until you
call `.collect()` (which returns a list of tuples) or `print()` the
expression (which renders a table).

**Selection** — keep rows matching a condition:

```python
>>> print(s.select(s.city == "London"))
┌─────┬───────┬────────┬────────┐
│ sno │ sname │ status │ city   │
├─────┼───────┼────────┼────────┤
│ S1  │ Smith │ 20     │ London │
│ S4  │ Clark │ 20     │ London │
└─────┴───────┴────────┴────────┘
(2 rows)
```

**Projection** — keep only certain columns:

```python
>>> print(s.project("sname", "city"))
┌───────┬────────┐
│ sname │ city   │
├───────┼────────┤
│ Smith │ London │
│ Jones │ Paris  │
│ Blake │ Paris  │
│ Clark │ London │
│ Adams │ Athens │
└───────┴────────┘
(5 rows)
```

**Chaining** — operations compose naturally:

```python
# "Names of London suppliers"
>>> s.select(s.city == "London").project("sname").collect()
[('Smith',), ('Clark',)]
```

### Predicates

Comparisons on attributes build a predicate tree instead of evaluating.
Combine them with `&` (and), `|` (or), and `~` (not):

```python
# Compound predicate
s.select((s.city == "London") & (s.status > 10))

# Negation
s.select(~(s.city == "Paris"))
```

> **Note:** Python's `and`, `or`, and `not` keywords cannot be overloaded.
> If you accidentally write `s.city == "London" and s.status > 10`,
> coddpiece will raise a helpful error telling you to use `&` instead.

### Joins

**Natural join** matches on shared attribute names:

```python
# Enrich shipments with supplier details (shared attribute: sno)
>>> print(sp.select(sp.pno == "P2").join(s).project("sname"))
┌───────┐
│ sname │
├───────┤
│ Smith │
│ Jones │
│ Blake │
│ Clark │
└───────┘
(4 rows)
```

**Equijoin** lets you specify which attributes to match when names differ:

```python
employees.equijoin(departments, "department", "name")
```

**Theta join** takes an arbitrary predicate:

```python
a.theta_join(b, a.x == b.y)
```

**Semijoin** keeps left rows that have *any* match in the right:

```python
# Suppliers who appear in at least one shipment
s.semijoin(sp)
```

**Antijoin** keeps left rows with *no* match — the complement of semijoin:

```python
# Suppliers with no shipments at all
>>> print(s.antijoin(sp).project("sname"))
┌───────┐
│ sname │
├───────┤
│ Adams │
└───────┘
(1 row)
```

**Outer join** preserves unmatched rows (with NULLs for missing values):

```python
s.outer_join(sp, how="left")   # all suppliers, even without shipments
s.outer_join(sp, how="right")  # all shipments, even without suppliers
s.outer_join(sp, how="full")   # both
```

> **Note:** `how="right"` and `how="full"` require **SQLite 3.39+** (2022),
> when SQLite added `RIGHT`/`FULL OUTER JOIN`. `how="left"` works on all
> supported versions. PostgreSQL and MySQL support all three.

### Set Operations

These require both relations to have *identical schemas* (same attribute
names and types):

```python
s_cities = s.project("city")
p_cities = p.project("city")

# Cities that have a supplier OR a part
s_cities.union(p_cities)

# Cities that have BOTH a supplier AND a part
>>> print(s_cities.intersect(p_cities))
┌────────┐
│ city   │
├────────┤
│ London │
│ Paris  │
└────────┘
(2 rows)

# Cities with a supplier but NO part
s_cities.difference(p_cities)   # → Athens
```

If schemas don't match, the error explains exactly what's wrong:

```
SchemaError: UNION requires identical schemas.

  Left (s):  {sno: str, sname: str, status: int, city: str}
  Right (p): {pno: str, pname: str, color: str, weight: float, city: str}

  Only in left:  sname, sno, status
  Only in right: color, pname, pno, weight
  Common:        city

  Hint: Use PROJECT to align schemas before UNION.
```

### Rename

Rename attributes to make schemas compatible, or to clarify results:

```python
s.project("sname").rename(name="sname") \
    .union(p.project("pname").rename(name="pname"))
```

### Aggregation

```python
from coddpiece import count, sum_, avg, min_, max_

>>> print(sp.group("sno", num_parts=count("pno"), total_qty=sum_("qty")))
┌─────┬───────────┬───────────┐
│ sno │ num_parts │ total_qty │
├─────┼───────────┼───────────┤
│ S1  │ 6         │ 1300      │
│ S2  │ 2         │ 700       │
│ S3  │ 1         │ 200       │
│ S4  │ 3         │ 900       │
└─────┴───────────┴───────────┘
(4 rows)
```

Aggregate without grouping keys to summarize an entire relation:

```python
sp.group(total=count())   # → single row: 12
```

### Division

The most powerful and least understood operation in relational algebra.
Division answers questions of the form "find X associated with **ALL** Y":

```python
# "Which suppliers supply ALL red parts?"
red_parts = p.select(p.color == "Red").project("pno")
result = sp.project("sno", "pno").divide(red_parts)
>>> result.collect()
[('S1',)]
```

Only S1 supplies every red part (P1, P4, and P6). S4 supplies P4 but
not P1 or P6, so S4 doesn't make the cut.

### The Algebra → SQL Mapping

This is the core teaching feature. Every expression can explain itself:

```python
>>> print(s.select(s.city == "London").project("sname").explain())
Algebra:
  π(sname)(σ(city="London")(s))

Tree:
  Project(sname)
  └─ Selection(city="London")
     └─ s

SQL:
  SELECT DISTINCT "sname"
  FROM "s"
  WHERE "city" = ?
  -- params: ['London']

Reading:
  Keep only rows where city="London". Keep only columns sname.
```

And for division, where the SQL mapping is genuinely revelatory:

```
Algebra:
  (π(sno,pno)(sp) ÷ π(pno)(σ(color="Red")(p)))

Tree:
  Division
  ├─ Project(sno, pno)
  │  └─ sp
  └─ Project(pno)
     └─ Selection(color="Red")
        └─ p

SQL:
  SELECT DISTINCT t1."sno"
  FROM (SELECT DISTINCT "sno", "pno"
  FROM "sp") AS t1
  WHERE NOT EXISTS (SELECT "pno"
    FROM (SELECT DISTINCT "pno"
      FROM "p"
    WHERE "color" = ?)
    EXCEPT SELECT t2."pno"
    FROM (SELECT DISTINCT "sno", "pno"
    FROM "sp") AS t2
  WHERE t2."sno" = t1."sno")
  -- params: ['Red']

Reading:
  Find sno associated with ALL pno in p.
```

The `NOT EXISTS ... EXCEPT` pattern is division's SQL translation: "find
suppliers where no red part is missing from their supply list." Seeing
the algebra and the SQL side by side makes the double negation click.

### Set vs. Bag Semantics

The relational model is set-based: no duplicate rows, ever. SQL defaults
to bag semantics (duplicates allowed). coddpiece uses set semantics by
default — every query includes `DISTINCT`.

To see the difference, use `.bags()`:

```python
>>> sp.project("sno").count()          # Set: 4 distinct suppliers
4
>>> sp.project("sno").bags().count()   # Bag: 12 rows (one per shipment)
12
```

### Using Your Own Database

coddpiece works with any [PEP 249](https://peps.python.org/pep-0249/)
connection. SQLite ships with Python; for PostgreSQL, install psycopg:

```python
# SQLite (zero setup)
import sqlite3
engine = Engine(sqlite3.connect(":memory:"))

# PostgreSQL
import psycopg
engine = Engine(psycopg.connect("dbname=mydb"))

# MySQL
import pymysql
engine = Engine(pymysql.connect(db="mydb"))
```

coddpiece auto-detects the connection's parameter style (`?` vs `%s`),
identifier quoting, and introspects table schemas from the database.

> **Backend coverage status.** SQLite is exercised by every test in CI.
> PostgreSQL and MySQL paths are implemented — paramstyle, quoting, and
> schema introspection branches all exist — but are not currently run
> against live databases in CI. If you use coddpiece on PG or MySQL and
> spot a regression in those paths, please open an issue.
>
> Note that `RIGHT`/`FULL OUTER JOIN` require **SQLite 3.39+** (2022);
> older SQLite builds will reject those queries.

### Complete Operation Reference

| Operation | Method | Algebra | SQL Pattern |
|-----------|--------|---------|-------------|
| Selection | `.select(pred)` | σ | `WHERE` |
| Projection | `.project(*cols)` | π | `SELECT cols` |
| Rename | `.rename(new='old')` | ρ | `AS` |
| Cross Product | `.cross(other)` | × | `FROM a, b` |
| Natural Join | `.join(other)` | ⋈ | `JOIN ... ON` (common cols) |
| Theta Join | `.theta_join(other, pred)` | ⋈θ | `JOIN ... ON pred` |
| Equijoin | `.equijoin(other, l, r)` | ⋈= | `JOIN ... ON l = r` |
| Semijoin | `.semijoin(other)` | ⋉ | `WHERE EXISTS` |
| Antijoin | `.antijoin(other)` | ▷ | `WHERE NOT EXISTS` |
| Left Outer Join | `.outer_join(other, 'left')` | ⟕ | `LEFT OUTER JOIN` |
| Right Outer Join | `.outer_join(other, 'right')` | ⟖ | `RIGHT OUTER JOIN` |
| Full Outer Join | `.outer_join(other, 'full')` | ⟗ | `FULL OUTER JOIN` |
| Union | `.union(other)` | ∪ | `UNION` |
| Intersection | `.intersect(other)` | ∩ | `INTERSECT` |
| Difference | `.difference(other)` | − | `EXCEPT` |
| Division | `.divide(other)` | ÷ | `NOT EXISTS ... EXCEPT` |
| Grouping | `.group(*keys, **aggs)` | γ | `GROUP BY` |

---

## Part II: What Is Relational Algebra?

If you already know relational algebra, you can stop reading. The rest is
for developers who've used SQL for years but never formally studied the
theory underneath it.

### The Big Idea

In 1970, Edgar F. Codd published "A Relational Model of Data for Large
Shared Data Banks." His insight was that data could be modeled as
**relations** — essentially named tables with typed columns — and
manipulated through a small, closed set of algebraic operations.

"Algebraic" means two things here. First, every operation takes one or
two relations as input and produces a relation as output. You can compose
them freely, just like arithmetic: if `3 + 4` gives you a number, you can
feed that number into another operation (`(3 + 4) × 2`). This property is
called **closure**: the operations are closed over the type "relation."

Second, the operations obey algebraic laws — associativity, commutativity,
distributivity — which means expressions can be **rewritten** into
equivalent forms. This is exactly what a query optimizer does: it takes
your SQL, translates it to an algebra expression, and rewrites it into a
more efficient equivalent form.

SQL was designed as a human-friendly surface syntax for this algebra.
Most of SQL's apparent complexity vanishes once you see the algebraic
operation it encodes.

### Relations, Tuples, and Attributes

A **relation** is a set of tuples, all sharing the same structure. Each
tuple is a row; each position in the tuple is an **attribute** (column)
with a name and a **domain** (type).

The word "set" is important. In pure relational algebra:

- There are no duplicate rows.
- There is no row ordering.
- Rows are identified by their values, not by position or ID.

This is where SQL departs from the model: SQL tables can have duplicates
(`SELECT` vs `SELECT DISTINCT`) and do have an implicit order. coddpiece
defaults to set semantics to keep the algebra honest.

### The Eight Original Operations

Codd defined eight fundamental operations. Every query you can write in
SQL can be expressed as some combination of these.

#### Selection (σ) — "Filter Rows"

Selection keeps only the rows satisfying a condition. It doesn't change
the columns; it changes which rows you see.

```
σ(city="London")(Suppliers)  →  SQL: SELECT * FROM Suppliers WHERE city = 'London'
```

#### Projection (π) — "Pick Columns"

Projection keeps only specified columns and eliminates duplicates.

```
π(sname)(Suppliers)  →  SQL: SELECT DISTINCT sname FROM Suppliers
```

Selection and projection are complementary: selection filters vertically
(which rows), projection filters horizontally (which columns).

#### Rename (ρ) — "Rename Columns"

Rename changes attribute names without changing data. It's the algebra's
equivalent of SQL's `AS`.

```
ρ(supplier_name/sname)(Suppliers)  →  SQL: SELECT sname AS supplier_name FROM Suppliers
```

You need rename to make schemas compatible for set operations, or to
disambiguate columns before a cross product.

#### Cross Product (×) — "Every Combination"

The cross product of two relations produces every possible combination
of their rows. If the left has 5 rows and the right has 6, the result
has 30 rows.

```
Suppliers × Parts  →  SQL: SELECT * FROM Suppliers, Parts
```

Cross products are rarely useful by themselves, but they're the
foundation of joins: a join is a cross product followed by a selection.

#### Natural Join (⋈) — "Match on Shared Columns"

The natural join combines two relations by matching on all columns
they have in common. It's by far the most important binary operation.

```
Shipments ⋈ Suppliers  →  Joins on shared column "sno"
```

Conceptually, a natural join is: take the cross product, keep only
rows where the shared columns agree, then remove the duplicate columns.
In SQL this maps to `JOIN ... ON` or `JOIN ... USING`.

#### Union (∪), Intersection (∩), Difference (−)

The set operations work on relations with identical schemas:

- **Union**: rows in either relation (SQL: `UNION`)
- **Intersection**: rows in both (SQL: `INTERSECT`)
- **Difference**: rows in the first but not the second (SQL: `EXCEPT`)

These require **schema compatibility**: both relations must have the
same attributes with the same types.

### Extended Operations

Codd's eight operations are theoretically complete — you can express any
query with them. But some common patterns are so useful they've been
given their own names.

#### Theta Join (⋈θ) and Equijoin

A **theta join** is a cross product followed by a selection on an
arbitrary condition. An **equijoin** is the special case where the
condition is equality between two columns.

```python
employees.equijoin(departments, "dept_id", "id")
# SQL: SELECT * FROM employees JOIN departments ON employees.dept_id = departments.id
```

Most joins you write in SQL are equijoins.

#### Semijoin (⋉) and Antijoin (▷)

The **semijoin** returns left rows that have at least one match in the
right. It doesn't add any columns from the right — it's a filtering
operation.

```python
s.semijoin(sp)    # Suppliers who ship at least one part
# SQL: SELECT * FROM s WHERE EXISTS (SELECT 1 FROM sp WHERE s.sno = sp.sno)
```

The **antijoin** is its complement: left rows with *no* match in the right.

```python
s.antijoin(sp)    # Suppliers who ship nothing
# SQL: SELECT * FROM s WHERE NOT EXISTS (SELECT 1 FROM sp WHERE s.sno = sp.sno)
```

Every time you write `WHERE [NOT] EXISTS` in SQL, you're expressing a
semijoin or antijoin.

#### Outer Join (⟕ ⟖ ⟗)

A regular join drops rows that have no match. An **outer join** preserves
them, filling in NULLs for the missing side.

NULLs don't exist in Codd's original algebra — outer joins are one of the
places where SQL extends beyond the pure relational model. This is itself
a useful thing to understand.

#### Division (÷) — "For ALL"

Division is the hardest operation and the one with no direct SQL keyword.
It answers universal quantification: "find X that is associated with
**all** Y."

Given `R(a, b) ÷ S(b)`, division returns all values of `a` in R that
appear paired with *every* value of `b` in S.

```
"Suppliers who supply ALL red parts"
= Shipments(sno, pno) ÷ RedParts(pno)
```

In SQL, this requires the `NOT EXISTS ... EXCEPT` double-negation pattern,
which is notoriously hard to write and harder to understand. The algebra
makes the intent obvious: you're dividing one relation by another, and the
result is the "quotient."

The name comes from the analogy with arithmetic: if `a × b = c`, then
`c ÷ b = a`. Similarly, if the cross product of the quotient with the
divisor is a subset of the dividend, the division is correct.

#### Aggregation (γ) — "Group and Summarize"

Aggregation groups rows by some attributes and computes summary values
(count, sum, average, etc.) over each group. It wasn't in Codd's
original 1970 paper but was added as a practical necessity.

```
γ(department; avg_salary←AVG(salary))(Employees)
SQL: SELECT department, AVG(salary) AS avg_salary FROM Employees GROUP BY department
```

### Why This Matters

If all you do is write SQL, why should you care about the algebra?

**Optimization.** Every database engine translates your SQL into an
algebra expression tree, rearranges it using algebraic laws (push
selections down, reorder joins), and then executes the optimized tree.
When you read an `EXPLAIN ANALYZE` plan, you're looking at an algebra
tree.

**Correctness.** The algebra gives you a vocabulary to think precisely
about what a query does. "This is a semijoin" is more useful than "this
is that thing with `WHERE EXISTS`."

**Composability.** SQL's syntax makes some compositions awkward (subqueries
in `FROM`, correlated subqueries in `WHERE`). The algebra makes everything
uniform: an operation takes relations and produces a relation, full stop.

**Division.** Once you understand relational division, you'll never again
stare at a "for all" query wondering how to write the `NOT EXISTS` inside
the `EXCEPT` inside the `NOT EXISTS`. You'll just think "that's division"
and write the SQL directly.

### Further Reading

- E.F. Codd, ["A Relational Model of Data for Large Shared Data Banks"](https://www.seas.upenn.edu/~zives/03f/cis550/codd.pdf) (1970).
  The paper that started it all. Twelve pages. Still readable.
- C.J. Date, *An Introduction to Database Systems* (8th edition).
  The definitive textbook. The suppliers-and-parts dataset used in
  coddpiece comes from here.
- Alice, *[Use The Index, Luke](https://use-the-index-luke.com/)*.
  Practical SQL performance, grounded in how the algebra gets executed.

---

## Development

```bash
git clone https://github.com/pgexperts/coddpiece
cd coddpiece
pip install -e '.[dev]'   # pytest, ruff, mypy
```

Run the same checks CI does:

```bash
pytest              # SQLite suite; the PostgreSQL tests skip unless DATABASE_URL is set
ruff check .
mypy coddpiece
```

The internals are documented separately: see
**[ARCHITECTURE.md](ARCHITECTURE.md)** for the module map and the load-bearing
invariants, and **[THEORY.md](THEORY.md)** for the design rationale — why
predicates hijack Python's comparison operators, why the expression tree uses
identity equality, and the rest.

---

## License

MIT
