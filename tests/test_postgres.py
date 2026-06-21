"""PostgreSQL-targeted tests.

These exercise the dialect branches that the main SQLite suite cannot
reach: psycopg's `pyformat` paramstyle (`%(p0)s` placeholders bound from
a dict; the bare `format`/`%s` style belongs to pg8000, not psycopg),
introspection via `information_schema`, the division SQL whose unaliased
derived tables PostgreSQL strictly rejects, and bag-mode INTERSECT ALL /
EXCEPT ALL (which SQLite does not implement at all).

Every test depends on the `pg_engine` fixture in conftest.py — that
fixture skips the test when `DATABASE_URL` is not set, so this file
is a no-op locally unless the user opts in by exporting the variable.
CI sets it via the postgres:16 service container. Each test runs in
its own freshly-created PG schema; teardown drops the schema with
CASCADE so leftover objects from a failed test never bleed forward.
"""

import pytest

from coddpiece.errors import DomainError


class TestPGEngineBasics:
    # The psycopg paramstyle path is exercised by every successful
    # query below. If paramstyle detection fails (the bug Cohort 1
    # closed by narrowing _detect_paramstyle's exception swallow), the
    # compiler emits qmark placeholders and psycopg rejects them with
    # an opaque "the syntax of the SQL string is invalid" error — these
    # tests would all fail loudly rather than silently producing wrong
    # results.
    def test_create_and_round_trip(self, pg_engine):
        r = pg_engine.create(
            "pg_basic",
            {"id": int, "name": str},
            rows=[(1, "alpha"), (2, "beta")],
        )
        rows = sorted(r.collect())
        assert rows == [(1, "alpha"), (2, "beta")]

    def test_introspect_existing_table(self, pg_engine):
        # information_schema branch — distinct from the SQLite PRAGMA
        # branch. Confirms the SELECT column_name, data_type query and
        # the SQL_TO_PYTHON mapping cooperate.
        pg_engine.create(
            "pg_intro",
            {"x": int, "y": str, "z": float},
            rows=[(1, "a", 1.5)],
        )
        # Re-wrap to force introspection through information_schema.
        wrapped = pg_engine.relation("pg_intro")
        assert wrapped.schema().names() == ("x", "y", "z")
        assert wrapped.schema().domains() == (int, str, float)

    def test_select_with_string_param(self, pg_engine):
        # Forces a parameterized SELECT through psycopg's `pyformat`
        # paramstyle: the compiler emits %(p0)s and binds "alpha" as a
        # dict-keyed parameter (via Dialect.format_params). A regression in
        # placeholder generation would surface here as a driver-level error.
        r = pg_engine.create(
            "pg_param",
            {"id": int, "name": str},
            rows=[(1, "alpha"), (2, "beta"), (3, "alpha")],
        )
        result = r.select(r.name == "alpha").collect()
        assert sorted(rid for rid, _ in result) == [1, 3]


class TestPGJoinsAndSetOps:
    # Joins compile to the same SQL shape on both backends, but the
    # column-order fix (Cohort 1 #1) and the alias rules behave the same
    # everywhere only if the per-side paramstyle and quoting agree. A
    # single representative test per family is enough — the SQL paths
    # are dialect-independent once paramstyle is right.
    def test_natural_join(self, pg_engine):
        a = pg_engine.create(
            "pg_nj_a",
            {"x": int, "sno": int, "y": int},
            rows=[(10, 1, 100)],
        )
        b = pg_engine.create("pg_nj_b", {"sno": int, "z": int}, rows=[(1, 42)])
        # Same shape as the SQLite regression test; a column-order
        # regression would mis-pair values across columns here too.
        assert a.join(b).collect() == [(10, 1, 100, 42)]

    def test_outer_join_full(self, pg_engine):
        # SQLite supports FULL OUTER JOIN as of 3.39 (March 2022) — most
        # CI runners have it now. PG has supported it forever. This is
        # mainly a smoke check that COALESCE on common attrs works.
        a = pg_engine.create(
            "pg_oj_a",
            {"x": int, "sno": int},
            rows=[(10, 1), (20, 2)],
        )
        b = pg_engine.create(
            "pg_oj_b",
            {"sno": int, "z": int},
            rows=[(1, 42), (3, 77)],
        )
        rows = a.outer_join(b, how="full").collect()
        snos = {r[1] for r in rows}
        assert snos == {1, 2, 3}

    def test_set_op_union(self, pg_engine):
        a = pg_engine.create("pg_so_a", {"x": int}, rows=[(1,), (2,)])
        b = pg_engine.create("pg_so_b", {"x": int}, rows=[(2,), (3,)])
        assert sorted(r[0] for r in a.union(b).collect()) == [1, 2, 3]


class TestPGDivision:
    # The Cohort 1 fix that gave division's inner divisor subquery an
    # alias is the entire reason this test exists: pre-fix, PG rejected
    # the SQL outright with "subquery in FROM must have an alias" while
    # SQLite tolerated it. If the fix regresses, this is the test that
    # catches it.
    def test_division_round_trip(self, pg_engine):
        sp = pg_engine.create(
            "pg_div_sp",
            {"sno": str, "pno": str},
            rows=[
                ("S1", "P1"), ("S1", "P2"),
                ("S2", "P1"),
            ],
        )
        all_p = pg_engine.create(
            "pg_div_p",
            {"pno": str},
            rows=[("P1",), ("P2",)],
        )
        # Suppliers who supply ALL parts {P1, P2}. Only S1 qualifies.
        result = sp.divide(all_p).collect()
        assert {r[0] for r in result} == {"S1"}

    def test_division_domain_mismatch_still_caught(self, pg_engine):
        # Cohort 1 #5: domain check for shared attribute names. PG would
        # silently coerce some str/int comparisons; we want construction
        # to fail before any SQL runs.
        a = pg_engine.create(
            "pg_dom_a",
            {"sno": str, "pno": str},
            rows=[("S1", "P1")],
        )
        b = pg_engine.create("pg_dom_b", {"pno": int}, rows=[(1,)])
        with pytest.raises(DomainError, match="domains"):
            a.divide(b)


class TestPGBagSemantics:
    # The SQLite suite asserts that bag-mode INTERSECT/EXCEPT *raise*
    # NotImplementedError. PG implements both `... ALL` forms, so here
    # we assert they SUCCEED with multiset semantics. The two suites
    # together pin both halves of the dialect-aware bag-mode contract.
    def test_bags_over_union_all(self, pg_engine):
        a = pg_engine.create("pg_bu_a", {"x": int}, rows=[(1,), (2,)])
        b = pg_engine.create("pg_bu_b", {"x": int}, rows=[(2,), (3,)])
        bag_rows = sorted(r[0] for r in a.union(b).bags().collect())
        assert bag_rows == [1, 2, 2, 3]

    def test_bags_over_intersect_all(self, pg_engine):
        # Multiset INTERSECT ALL: the count of each value in the result
        # is min(count_left, count_right). Pre-Cohort-B this was a no-op
        # via string replacement; on PG that meant set semantics. With
        # the bag_mode flag threaded through the compiler, PG actually
        # honors INTERSECT ALL here.
        a = pg_engine.create("pg_bi_a", {"x": int}, rows=[(1,), (1,), (2,)])
        b = pg_engine.create(
            "pg_bi_b", {"x": int}, rows=[(1,), (1,), (1,), (2,)]
        )
        bag_rows = sorted(r[0] for r in a.intersect(b).bags().collect())
        assert bag_rows == [1, 1, 2]

    def test_bags_over_except_all(self, pg_engine):
        # EXCEPT ALL keeps left-side duplicates beyond what right has:
        # 1 appears 3 times on left, 1 time on right → 2 copies survive.
        a = pg_engine.create(
            "pg_be_a", {"x": int}, rows=[(1,), (1,), (1,), (2,)]
        )
        b = pg_engine.create("pg_be_b", {"x": int}, rows=[(1,)])
        bag_rows = sorted(r[0] for r in a.difference(b).bags().collect())
        assert bag_rows == [1, 1, 2]


class TestPGGrouping:
    def test_group_count_and_sum(self, pg_engine):
        from coddpiece import count, sum_
        sp = pg_engine.create(
            "pg_grp",
            {"sno": str, "pno": str, "qty": int},
            rows=[
                ("S1", "P1", 100),
                ("S1", "P2", 200),
                ("S2", "P1", 50),
            ],
        )
        result = sp.group(
            "sno", n=count("pno"), total=sum_("qty")
        ).collect()
        by_sno = {row[0]: (row[1], row[2]) for row in result}
        assert by_sno["S1"] == (2, 300)
        assert by_sno["S2"] == (1, 50)


class TestNestedSetOpsPostgres:
    # Cross-backend confirmation of the nested set-op fix: the compiler
    # subquery-wraps any operand that is itself a set operation, e.g.
    # "... EXCEPT SELECT cols FROM (b EXCEPT c) AS tN". PostgreSQL REQUIRES
    # the derived-table alias (SQLite merely tolerates it), so this is where
    # the chosen wrap form is actually load-bearing for portability.
    def test_except_right_nesting_pg(self, pg_engine):
        a = pg_engine.create("pnse_a", {"x": int}, rows=[(1,), (2,), (3,)])
        b = pg_engine.create("pnse_b", {"x": int}, rows=[(2,), (3,), (4,)])
        c = pg_engine.create("pnse_c", {"x": int}, rows=[(3,), (4,), (5,)])
        expr = a.difference(b.difference(c))
        assert sorted(r[0] for r in expr.collect()) == [1, 3]

    def test_union_over_intersect_pg(self, pg_engine):
        # PostgreSQL gives INTERSECT higher precedence than UNION, so the
        # unwrapped flat form would mis-evaluate here; the wrap makes the
        # tree's grouping explicit and portable.
        a = pg_engine.create("pnui_a", {"x": int}, rows=[(1,), (2,), (3,)])
        b = pg_engine.create("pnui_b", {"x": int}, rows=[(2,), (3,), (4,)])
        c = pg_engine.create("pnui_c", {"x": int}, rows=[(3,), (4,), (5,)])
        expr = a.union(b.intersect(c))
        assert sorted(r[0] for r in expr.collect()) == [1, 2, 3, 4]

    def test_nested_intersect_all_pg(self, pg_engine):
        # PostgreSQL DOES implement INTERSECT ALL, so bag mode over nested
        # INTERSECT must execute (not raise) and preserve multiset semantics.
        d = pg_engine.create("pnia_d", {"x": int}, rows=[(1,), (1,), (2,), (3,)])
        e = pg_engine.create("pnia_e", {"x": int}, rows=[(1,), (1,), (2,)])
        f = pg_engine.create("pnia_f", {"x": int}, rows=[(1,), (2,)])
        # e ∩ALL f over [1,1,2] and [1,2] = [1,2]; d ∩ALL [1,2] = [1,2].
        rows = sorted(r[0] for r in d.intersect(e.intersect(f)).bags().collect())
        assert rows == [1, 2]
