"""Comprehensive tests for the relational algebra package.

End-to-end coverage for schema → predicates → relation → compiler → engine,
plus display, aggregates, and error paths. All tests run against SQLite
in-memory via the shared fixtures in conftest.py (`engine`, `sp_data`,
`emp_data`). The classes below are grouped into "phases" that mirror the
teaching progression of the library — foundation, set ops, joins,
aggregation, division, display, polish — so new tests should slot into
the phase whose invariants they exercise.
"""

import pytest

from coddpiece import (
    Attribute,
    DomainError,
    PredicateError,
    Schema,
    SchemaError,
    count,
    sum_,
)
from coddpiece.errors import AttributeError_
from coddpiece.predicates import Predicate

# ===================================================================
# Phase 1: Foundation
# ===================================================================
# Protects the building blocks: Schema/Attribute construction and algebra,
# Engine bootstrap (create + introspect), Attr-proxy predicate capture, and
# the primitive Selection/Projection operators. These tests guard the
# "eager validation at construction" invariant — schema errors must surface
# before any SQL runs.


class TestSchema:
    # Schema algebra is pure Python; no engine required. These tests lock in
    # the compatibility rules (order-insensitive), disjoint-compose contract,
    # and eager rejection of duplicates / unsupported domains / bad names.
    def test_create_schema(self):
        s = Schema((Attribute("name", str), Attribute("age", int)))
        assert s.names() == ("name", "age")
        assert s.domains() == (str, int)
        assert len(s) == 2

    def test_duplicate_names_rejected(self):
        with pytest.raises(SchemaError, match="Duplicate"):
            Schema((Attribute("x", int), Attribute("x", int)))

    def test_unsupported_domain(self):
        with pytest.raises(DomainError, match="Unsupported"):
            Attribute("x", list)

    def test_invalid_name(self):
        with pytest.raises(AttributeError_, match="Invalid"):
            Attribute("1bad", str)

    def test_compatible(self):
        s1 = Schema((Attribute("a", int), Attribute("b", str)))
        s2 = Schema((Attribute("b", str), Attribute("a", int)))  # different order
        assert s1.compatible(s2)

    def test_not_compatible(self):
        s1 = Schema((Attribute("a", int),))
        s2 = Schema((Attribute("a", str),))
        assert not s1.compatible(s2)

    def test_project(self):
        s = Schema((Attribute("a", int), Attribute("b", str), Attribute("c", float)))
        p = s.project("b", "a")
        assert p.names() == ("b", "a")

    def test_project_unknown(self):
        s = Schema((Attribute("a", int),))
        with pytest.raises(AttributeError_, match="unknown"):
            s.project("z")

    def test_compose(self):
        s1 = Schema((Attribute("a", int),))
        s2 = Schema((Attribute("b", str),))
        c = s1.compose(s2)
        assert c.names() == ("a", "b")

    def test_compose_collision(self):
        s1 = Schema((Attribute("a", int),))
        s2 = Schema((Attribute("a", str),))
        with pytest.raises(SchemaError, match="disjoint"):
            s1.compose(s2)

    def test_common(self):
        s1 = Schema((Attribute("a", int), Attribute("b", str)))
        s2 = Schema((Attribute("b", str), Attribute("c", float)))
        c = s1.common(s2)
        assert c.names() == ("b",)

    def test_rename(self):
        s = Schema((Attribute("a", int), Attribute("b", str)))
        r = s.rename(x="a")
        assert r.names() == ("x", "b")

    def test_join_compose(self):
        s1 = Schema((Attribute("a", int), Attribute("b", str)))
        s2 = Schema((Attribute("b", str), Attribute("c", float)))
        j = s1.join_compose(s2)
        assert j.names() == ("a", "b", "c")


class TestEngine:
    def test_create_and_introspect(self, engine):
        r = engine.create("test", {"x": int, "y": str}, rows=[(1, "a"), (2, "b")])
        assert r.schema().names() == ("x", "y")
        rows = r.collect()
        assert len(rows) == 2
        assert (1, "a") in rows

    def test_relation_wraps_existing(self, sp_data):
        s, p, sp, engine = sp_data
        assert "sno" in s.schema()
        assert len(s.collect()) == 5


class TestPredicates:
    # Exercises the Attr proxy: Python comparison/logical operators must
    # produce Predicate nodes rather than evaluating to bool. The bool-trap
    # test protects the `__bool__` safeguard that catches `and`/`or` misuse.
    def test_attr_eq(self, sp_data):
        s, p, sp, engine = sp_data
        pred = s.city == "London"
        assert isinstance(pred, Predicate)
        assert pred.algebra() == 'city="London"'

    def test_compound_and(self, sp_data):
        s, p, sp, engine = sp_data
        pred = (s.city == "London") & (s.status > 10)
        assert "∧" in pred.algebra()

    def test_compound_or(self, sp_data):
        s, p, sp, engine = sp_data
        pred = (s.city == "London") | (s.city == "Paris")
        assert "∨" in pred.algebra()

    def test_not(self, sp_data):
        s, p, sp, engine = sp_data
        pred = ~(s.city == "London")
        assert "¬" in pred.algebra()

    def test_bool_trap(self, sp_data):
        s, p, sp, engine = sp_data
        with pytest.raises(PredicateError, match="Use '&'"):
            # The "useless expression" is the entire point: `and` triggers
            # __bool__ on the left Predicate, which is the trap under test.
            (s.city == "London") and (s.status > 10)  # noqa: B018


class TestSelection:
    def test_simple_select(self, sp_data):
        s, p, sp, engine = sp_data
        result = s.select(s.city == "London").collect()
        assert len(result) == 2
        assert all(row[3] == "London" for row in result)

    def test_compound_select(self, sp_data):
        s, p, sp, engine = sp_data
        result = s.select(
            (s.city == "London") & (s.status == 20)
        ).collect()
        assert len(result) == 2  # S1 and S4 both status 20, London

    def test_select_not(self, sp_data):
        s, p, sp, engine = sp_data
        result = s.select(~(s.city == "London")).collect()
        assert len(result) == 3
        assert all(row[3] != "London" for row in result)


class TestProjection:
    def test_simple_project(self, sp_data):
        s, p, sp, engine = sp_data
        result = s.project("sname").collect()
        assert len(result) == 5
        assert all(len(row) == 1 for row in result)

    def test_project_distinct(self, sp_data):
        s, p, sp, engine = sp_data
        result = s.project("city").collect()
        cities = {row[0] for row in result}
        assert cities == {"London", "Paris", "Athens"}
        assert len(result) == 3  # DISTINCT removes dupes

    def test_project_unknown_attr(self, sp_data):
        s, p, sp, engine = sp_data
        with pytest.raises(AttributeError_, match="unknown"):
            s.project("nonexistent")


class TestSelectProject:
    def test_chained(self, sp_data):
        s, p, sp, engine = sp_data
        # "Names of London suppliers"
        result = s.select(s.city == "London").project("sname").collect()
        names = {row[0] for row in result}
        assert names == {"Smith", "Clark"}


# ===================================================================
# Phase 2: Set Operations + Rename
# ===================================================================
# Union/intersect/difference require identical schemas; rename is the usual
# tool for reaching that state. The `test_rename_then_union` case is the
# canonical demo that these two features compose correctly.


class TestRename:
    def test_simple_rename(self, sp_data):
        s, p, sp, engine = sp_data
        r = s.rename(supplier_city="city")
        assert "supplier_city" in r.schema()
        assert "city" not in r.schema()
        rows = r.collect()
        assert len(rows) == 5

    def test_rename_preserves_data(self, sp_data):
        s, p, sp, engine = sp_data
        r = s.rename(supplier_city="city")
        # The renamed column should have the same data
        original_cities = {row[3] for row in s.collect()}
        renamed = r.collect()
        # Find the index of supplier_city
        idx = r.schema().names().index("supplier_city")
        renamed_cities = {row[idx] for row in renamed}
        assert original_cities == renamed_cities


class TestSetOps:
    def test_union(self, sp_data):
        s, p, sp, engine = sp_data
        # Cities from suppliers ∪ cities from parts
        s_cities = s.project("city")
        p_cities = p.project("city")
        result = s_cities.union(p_cities).collect()
        cities = {row[0] for row in result}
        assert cities == {"London", "Paris", "Athens", "Oslo"}

    def test_intersect(self, sp_data):
        s, p, sp, engine = sp_data
        # Cities with both a supplier and a part
        s_cities = s.project("city")
        p_cities = p.project("city")
        result = s_cities.intersect(p_cities).collect()
        cities = {row[0] for row in result}
        assert cities == {"London", "Paris"}

    def test_difference(self, sp_data):
        s, p, sp, engine = sp_data
        # Cities with suppliers but no parts
        s_cities = s.project("city")
        p_cities = p.project("city")
        result = s_cities.difference(p_cities).collect()
        cities = {row[0] for row in result}
        assert cities == {"Athens"}

    def test_union_incompatible(self, sp_data):
        s, p, sp, engine = sp_data
        with pytest.raises(SchemaError, match="identical schemas"):
            s.union(p)

    def test_rename_then_union(self, sp_data):
        s, p, sp, engine = sp_data
        # Rename to make schemas compatible, then union
        s_names = s.project("sname").rename(name="sname")
        p_names = p.project("pname").rename(name="pname")
        result = s_names.union(p_names).collect()
        names = {row[0] for row in result}
        assert "Smith" in names
        assert "Nut" in names


# ===================================================================
# Phase 3: Joins
# ===================================================================
# Covers the full join family: cross (disjoint schemas required),
# natural (auto-detects common attrs), theta (explicit predicate across
# relations), and equijoin (explicit attr pair). Name-collision and
# no-common-attr errors are eager schema checks.


class TestCrossProduct:
    def test_cross(self, engine):
        a = engine.create("a", {"x": int}, rows=[(1,), (2,)])
        b = engine.create("b", {"y": str}, rows=[("a",), ("b",)])
        result = a.cross(b).collect()
        assert len(result) == 4

    def test_cross_name_collision(self, sp_data):
        s, p, sp, engine = sp_data
        # s and p both have 'city' — should raise
        with pytest.raises(SchemaError, match="disjoint"):
            s.cross(p)


class TestNaturalJoin:
    def test_natural_join(self, sp_data):
        s, p, sp, engine = sp_data
        # sp ⋈ s joins on sno
        result = sp.join(s).collect()
        schema = sp.join(s).schema()
        assert "sno" in schema
        assert "sname" in schema
        assert "pno" in schema
        assert len(result) == 12  # One row per shipment, enriched

    def test_join_then_select_project(self, sp_data):
        s, p, sp, engine = sp_data
        # "Names of suppliers who supply P2"
        result = (
            sp.select(sp.pno == "P2")
            .join(s)
            .project("sname")
            .collect()
        )
        names = {row[0] for row in result}
        assert names == {"Smith", "Jones", "Blake", "Clark"}

    def test_no_common_attrs(self, engine):
        a = engine.create("a2", {"x": int}, rows=[(1,)])
        b = engine.create("b2", {"y": int}, rows=[(2,)])
        with pytest.raises(SchemaError, match="no common"):
            a.join(b)


class TestThetaJoin:
    def test_theta_join(self, engine):
        a = engine.create("left_t", {"x": int, "val": str}, rows=[(1, "a"), (2, "b")])
        b = engine.create("right_t", {"y": int, "data": str}, rows=[(1, "x"), (3, "z")])
        result = a.theta_join(b, a.x == b.y).collect()
        assert len(result) == 1
        assert result[0] == (1, "a", 1, "x")


class TestEquijoin:
    def test_equijoin(self, emp_data):
        employees, departments, engine = emp_data
        result = employees.equijoin(departments, "department", "name").collect()
        schema = employees.equijoin(departments, "department", "name").schema()
        assert "budget" in schema
        assert "location" in schema
        assert len(result) == 8  # Every employee matches a department


# ===================================================================
# Phase 4: Extended Joins
# ===================================================================
# Semijoin / antijoin / outer join. The suppliers-and-parts dataset is
# built so S5 has no shipments — these tests rely on that shape to
# distinguish the three variants.


class TestSemijoin:
    def test_semijoin(self, sp_data):
        s, p, sp, engine = sp_data
        # Suppliers who appear in shipments
        result = s.semijoin(sp).collect()
        snos = {row[0] for row in result}
        assert snos == {"S1", "S2", "S3", "S4"}  # S5 has no shipments
        assert len(result) == 4


class TestAntijoin:
    def test_antijoin(self, sp_data):
        s, p, sp, engine = sp_data
        # Suppliers with NO shipments
        result = s.antijoin(sp).collect()
        snos = {row[0] for row in result}
        assert snos == {"S5"}


class TestOuterJoin:
    def test_left_outer(self, sp_data):
        s, p, sp, engine = sp_data
        result = s.outer_join(sp, how="left").collect()
        snos = {row[0] for row in result}
        assert "S5" in snos  # S5 preserved even with no shipments
        assert len(result) == 13  # 12 shipments + 1 for S5 with NULLs


# ===================================================================
# Phase 5: Aggregation
# ===================================================================
# Group-by with AggSpec (count/sum_/avg/min_/max_). Includes the
# no-key / global aggregation case (single-row result) and verifies
# that the output schema pairs group keys with named aggregate columns.


class TestGrouping:
    def test_group_count(self, sp_data):
        s, p, sp, engine = sp_data
        result = sp.group("sno", num_parts=count("pno")).collect()
        result_dict = {row[0]: row[1] for row in result}
        assert result_dict["S1"] == 6
        assert result_dict["S3"] == 1

    def test_group_sum(self, sp_data):
        s, p, sp, engine = sp_data
        result = sp.group("sno", total_qty=sum_("qty")).collect()
        result_dict = {row[0]: row[1] for row in result}
        assert result_dict["S1"] == 1300  # 300+200+400+200+100+100

    def test_no_key_aggregation(self, sp_data):
        s, p, sp, engine = sp_data
        result = sp.group(total=count()).collect()
        assert len(result) == 1
        assert result[0][0] == 12

    def test_group_schema(self, sp_data):
        s, p, sp, engine = sp_data
        g = sp.group("sno", n=count("pno"), t=sum_("qty"))
        schema = g.schema()
        assert schema.names() == ("sno", "n", "t")


# ===================================================================
# Phase 6: Division
# ===================================================================
# Division is the odd one out: it is the only node the compiler visits
# twice in a single compile (dividend appears on both sides of the
# NOT EXISTS correlation). These tests exercise both the semantic
# "supplies ALL X" meaning and the eager schema check that the divisor
# attrs form a subset of the dividend's.


class TestDivision:
    def test_division_basic(self, sp_data):
        s, p, sp, engine = sp_data
        # "Suppliers who supply ALL red parts"
        red_parts = p.select(p.color == "Red").project("pno")
        result = sp.project("sno", "pno").divide(red_parts).collect()
        snos = {row[0] for row in result}
        # S1 supplies P1, P4, P6 (all red parts). S4 supplies P4 but not P1/P6.
        assert snos == {"S1"}

    def test_division_all_parts(self, sp_data):
        s, p, sp, engine = sp_data
        # "Suppliers who supply ALL parts" — only S1
        all_parts = p.project("pno")
        result = sp.project("sno", "pno").divide(all_parts).collect()
        snos = {row[0] for row in result}
        assert snos == {"S1"}

    def test_division_schema_validation(self, sp_data):
        s, p, sp, engine = sp_data
        # Divisor attrs not subset of dividend
        with pytest.raises(SchemaError, match="subset"):
            s.divide(p)


# ===================================================================
# Phase 7: Display
# ===================================================================
# Renderers: algebra notation (Greek symbols), tree, explain (combined
# multi-section view), and SQL. The SQL tests protect the parameterized-
# query invariant — literals like "London" must appear under a `params:`
# footer, never interpolated into the query string.


class TestAlgebra:
    def test_select_algebra(self, sp_data):
        s, p, sp, engine = sp_data
        expr = s.select(s.city == "London")
        alg = expr.algebra()
        assert "σ" in alg
        assert 'city="London"' in alg

    def test_project_algebra(self, sp_data):
        s, p, sp, engine = sp_data
        expr = s.project("sname", "city")
        alg = expr.algebra()
        assert "π" in alg
        assert "sname,city" in alg

    def test_join_algebra(self, sp_data):
        s, p, sp, engine = sp_data
        alg = sp.join(s).algebra()
        assert "⋈" in alg

    def test_division_algebra(self, sp_data):
        s, p, sp, engine = sp_data
        red = p.select(p.color == "Red").project("pno")
        expr = sp.project("sno", "pno").divide(red)
        alg = expr.algebra()
        assert "÷" in alg


class TestTree:
    def test_chained_tree(self, sp_data):
        s, p, sp, engine = sp_data
        expr = s.select(s.city == "London").project("sname")
        tree = expr.tree()
        assert "Project" in tree
        assert "Selection" in tree
        assert "s" in tree


class TestExplain:
    def test_explain_has_all_sections(self, sp_data):
        s, p, sp, engine = sp_data
        expr = s.select(s.city == "London").project("sname")
        explanation = expr.explain()
        assert "Algebra:" in explanation
        assert "Tree:" in explanation
        assert "SQL:" in explanation
        assert "Reading:" in explanation


class TestSQL:
    def test_sql_generation(self, sp_data):
        s, p, sp, engine = sp_data
        sql = s.select(s.city == "London").project("sname").sql()
        assert "SELECT" in sql
        assert "WHERE" in sql

    def test_sql_params_shown(self, sp_data):
        # Guards the parameterized-query invariant: the literal must be
        # carried as a parameter (visible via the `params:` footer) rather
        # than inlined into the SQL text.
        s, p, sp, engine = sp_data
        sql = s.select(s.city == "London").sql()
        assert "params:" in sql
        assert "London" in sql


# ===================================================================
# Phase 8: Polish
# ===================================================================
# Bag semantics (.bags() strips DISTINCT), __str__ table rendering,
# SQL pretty-printing, and a grab-bag of edge cases. `TestBags`
# specifically protects the set-vs-bag teaching distinction: the same
# projection must yield fewer rows with default set semantics than
# with .bags().


class TestBags:
    def test_bags_preserves_duplicates(self, sp_data):
        s, p, sp, engine = sp_data
        set_result = sp.project("sno").collect()
        bag_result = sp.project("sno").bags().collect()
        assert len(set_result) == 4   # 4 distinct suppliers
        assert len(bag_result) == 12  # 12 shipment rows

    def test_bags_iter(self, sp_data):
        s, p, sp, engine = sp_data
        rows = list(sp.project("sno").bags())
        assert len(rows) == 12

    def test_bags_count(self, sp_data):
        s, p, sp, engine = sp_data
        assert sp.project("sno").bags().count() == 12

    def test_bags_explain(self, sp_data):
        s, p, sp, engine = sp_data
        explanation = sp.project("sno").bags().explain()
        assert "Bag semantics" in explanation


class TestTableRendering:
    def test_str_renders_table(self, sp_data):
        s, p, sp, engine = sp_data
        output = str(s)
        assert "Smith" in output
        assert "sno" in output
        assert "(5 rows)" in output
        # Box drawing chars
        assert "┌" in output
        assert "│" in output
        assert "└" in output

    def test_str_single_row(self, sp_data):
        s, p, sp, engine = sp_data
        result = s.select(s.sno == "S1")
        output = str(result)
        assert "(1 row)" in output

    def test_empty_result(self, sp_data):
        s, p, sp, engine = sp_data
        result = s.select(s.city == "Nowhere")
        output = str(result)
        assert "(0 rows)" in output


class TestSQLFormatting:
    def test_format_has_newlines(self, sp_data):
        s, p, sp, engine = sp_data
        # A join query should have formatted output
        sql = sp.join(s).project("sname").sql()
        assert "\n" in sql


class TestEdgeCases:
    # Miscellaneous: .attr() escape hatch for non-identifier column names,
    # .schema()/.count()/__iter__ convenience surface, and a larger
    # end-to-end chained query that exercises selection + join + project
    # together. New integration-style tests belong here.
    def test_attr_escape_hatch(self, sp_data):
        s, p, sp, engine = sp_data
        # .attr() works same as dotted access
        pred = s.attr("city") == "London"
        result = s.select(pred).collect()
        assert len(result) == 2

    def test_schema_method(self, sp_data):
        s, p, sp, engine = sp_data
        schema = s.schema()
        assert schema.names() == ("sno", "sname", "status", "city")

    def test_count(self, sp_data):
        s, p, sp, engine = sp_data
        assert s.count() == 5
        assert s.select(s.city == "London").count() == 2

    def test_iter(self, sp_data):
        s, p, sp, engine = sp_data
        rows = list(s)
        assert len(rows) == 5

    def test_chained_complex_query(self, sp_data):
        s, p, sp, engine = sp_data
        # "Names and cities of suppliers who supply red parts, with qty > 200"
        result = (
            sp.select(sp.qty > 200)
            .join(p.select(p.color == "Red"))
            .join(s)
            .project("sname", "city")
            .collect()
        )
        names = {row[0] for row in result}
        assert "Smith" in names  # S1 supplies P1 qty 300 (Red)
        assert "Clark" in names  # S4 supplies P4 qty 300 (Red)

    def test_division_explain_has_reading(self, sp_data):
        s, p, sp, engine = sp_data
        red_parts = p.select(p.color == "Red").project("pno")
        expr = sp.project("sno", "pno").divide(red_parts)
        explanation = expr.explain()
        assert "ALL" in explanation
        assert "NOT EXISTS" in explanation


# ===================================================================
# Regressions
# ===================================================================
# Each test below pins a bug surfaced by code review. The shared discipline
# is to assert behavior the original code *almost* got right — column order
# matched the schema for the common test data but desynced for the general
# case; hasattr appeared to work but propagated the wrong exception type;
# Division compiled fine on SQLite but produced unportable SQL. Failing
# regressions here are the canary that the corresponding invariants have
# slipped again.


class TestNaturalJoinColumnOrder:
    # The original compiler emitted (common + left_only + right_only),
    # but Schema.join_compose returns left.attributes + right_only — which
    # preserves the common attribute's left-side position. Test data with
    # the common attribute NOT first on the left would expose the desync.
    def test_common_not_first_in_left(self, engine):
        a = engine.create(
            "njco_a",
            {"x": int, "sno": int, "y": int},
            rows=[(10, 1, 100), (20, 2, 200)],
        )
        b = engine.create(
            "njco_b",
            {"sno": int, "z": int},
            rows=[(1, 42), (2, 99)],
        )
        result = a.join(b)
        # Schema order: left attrs (x, sno, y) then right_only (z).
        assert result.schema().names() == ("x", "sno", "y", "z")
        rows = sorted(result.collect())
        # Column values must line up with schema order. Pre-fix this would
        # have come out as (sno, x, y, z) i.e. (1, 10, 100, 42).
        assert rows == [(10, 1, 100, 42), (20, 2, 200, 99)]


class TestOuterJoinColumnOrder:
    # Same column-order invariant as natural join, plus COALESCE for common
    # attrs. Tests cover all three directions; the previous suite only
    # exercised "left" with the suppliers-and-parts shape that happened to
    # have the common attribute first on the left.
    def test_left_outer_common_not_first(self, engine):
        a = engine.create(
            "ojco_a_l",
            {"x": int, "sno": int},
            rows=[(10, 1), (20, 2)],
        )
        b = engine.create(
            "ojco_b_l",
            {"sno": int, "z": int},
            rows=[(1, 42)],
        )
        result = a.outer_join(b, how="left")
        assert result.schema().names() == ("x", "sno", "z")
        rows = sorted(result.collect(), key=lambda r: r[0])
        assert rows[0] == (10, 1, 42)
        # Unmatched left row preserved; right-only column is NULL.
        assert rows[1][:2] == (20, 2) and rows[1][2] is None

    def test_right_outer_common_not_first(self, engine):
        # Right-outer preserves the RIGHT operand, so the unmatched row
        # belongs on b (the right side here). `a` is left so its schema
        # determines column order: (x, sno) followed by right_only (z).
        a = engine.create(
            "ojco_a_r",
            {"x": int, "sno": int},
            rows=[(10, 1)],
        )
        b = engine.create(
            "ojco_b_r",
            {"sno": int, "z": int},
            rows=[(1, 42), (2, 99)],
        )
        result = a.outer_join(b, how="right")
        assert result.schema().names() == ("x", "sno", "z")
        rows = sorted(result.collect(), key=lambda r: r[1])  # sort by sno
        # sno=1 matches both sides; sno=2 is right-only so left columns NULL.
        assert rows[0] == (10, 1, 42)
        assert rows[1][1:] == (2, 99) and rows[1][0] is None

    def test_full_outer_common_not_first(self, engine):
        a = engine.create(
            "ojco_a_f",
            {"x": int, "sno": int},
            rows=[(10, 1), (20, 2)],
        )
        b = engine.create(
            "ojco_b_f",
            {"sno": int, "z": int},
            rows=[(1, 42), (3, 77)],
        )
        result = a.outer_join(b, how="full")
        assert result.schema().names() == ("x", "sno", "z")
        rows = result.collect()
        # COALESCE on the common attribute must produce the matched value
        # for the joined row and the present side's value otherwise.
        snos_in_result = {r[1] for r in rows}
        assert snos_in_result == {1, 2, 3}


class TestAttributeErrorBuiltinProtocol:
    # AttributeError_ now multi-inherits from the builtin AttributeError so
    # hasattr / 3-arg getattr behave correctly. Without that, both leak the
    # custom exception out and break @cached_property and IDE introspection.
    def test_hasattr_returns_false_for_missing(self, sp_data):
        s, _p, _sp, _engine = sp_data
        assert hasattr(s, "sno") is True
        assert hasattr(s, "definitely_not_a_column") is False

    def test_getattr_default_returns_for_missing(self, sp_data):
        s, _p, _sp, _engine = sp_data
        sentinel = object()
        assert getattr(s, "definitely_not_a_column", sentinel) is sentinel


class TestDivisionDomainCheck:
    # Subset-by-name was previously sufficient. With names matching but
    # domains differing, the compiled EXCEPT compares mixed types — broken
    # in subtle, backend-dependent ways. Now caught at construction.
    def test_rejects_domain_mismatch(self, engine):
        dividend = engine.create(
            "divd_dom_a",
            {"sno": str, "pno": str},
            rows=[("S1", "P1")],
        )
        divisor = engine.create(
            "divd_dom_b",
            {"pno": int},
            rows=[(1,)],
        )
        with pytest.raises(DomainError, match="domains"):
            dividend.divide(divisor)


class TestDivisionSQLAlias:
    # The original division SQL had `FROM ({right_sql})` with no alias.
    # SQLite tolerated it; PostgreSQL rejects with "subquery in FROM must
    # have an alias." We can't run a Postgres test from this suite, but
    # we can pin the SQL shape so the alias doesn't regress.
    def test_emits_alias_on_inner_divisor_subquery(self, sp_data):
        s, p, sp, engine = sp_data
        red_parts = p.select(p.color == "Red").project("pno")
        expr = sp.project("sno", "pno").divide(red_parts)
        sql = expr.sql()
        # Compiler aliases derived tables with t1, t2, ... — the inner
        # divisor subquery used to lack any alias before this fix. Match
        # any "AS tN" attached to a parenthesized SELECT to avoid coupling
        # the test to specific alias numbering.
        import re
        # Strip the "-- params: ..." footer the .sql() helper appends so it
        # doesn't confuse the regex.
        sql_only = sql.split("-- params:")[0]
        assert re.search(r"\)\s+AS\s+t\d+", sql_only), (
            f"Expected an aliased derived table inside division SQL, got:\n{sql}"
        )


class TestEngineRelationValidation:
    # Engine.relation() is the public entry point for wrapping an existing
    # table. Pre-fix it interpolated the table name straight into a PRAGMA
    # statement, which is both an injection vector and a quoting bug for
    # reserved-word table names. The validation gate is now the same
    # invariant Attribute already enforces on column names.
    def test_rejects_non_identifier_table_name(self, engine):
        with pytest.raises(ValueError, match="identifier"):
            engine.relation('users"); DROP TABLE foo; --')

    def test_accepts_reserved_word_table_via_quoting(self, engine):
        # "order" is a Python identifier (passes isidentifier()) but a
        # SQL reserved word; the dialect's quote_identifier handles that.
        engine.create(
            "order",
            {"id": int, "qty": int},
            rows=[(1, 10), (2, 20)],
        )
        wrapped = engine.relation("order")
        assert wrapped.schema().names() == ("id", "qty")
        assert wrapped.count() == 2


class TestBagsOverSetOps:
    # Pre-fix, BagWrapper used a textual replace of "SELECT DISTINCT ".
    # That stripped the per-side DISTINCT but left bare UNION/INTERSECT/EXCEPT,
    # which are themselves set operators in SQL — so .bags() over a set
    # operation silently produced set semantics. Threading bag_mode through
    # the compiler turns those into UNION ALL / INTERSECT ALL / EXCEPT ALL,
    # so duplicates actually survive the round-trip now.
    def test_bags_over_union_preserves_duplicates(self, engine):
        a = engine.create("bsu_a", {"x": int}, rows=[(1,), (2,)])
        b = engine.create("bsu_b", {"x": int}, rows=[(2,), (3,)])
        # Set: 3 rows {1, 2, 3}. Bag: 4 rows [1, 2, 2, 3].
        assert sorted(r[0] for r in a.union(b).collect()) == [1, 2, 3]
        assert sorted(r[0] for r in a.union(b).bags().collect()) == [1, 2, 2, 3]

    def test_bags_over_intersect_raises_on_sqlite(self, engine):
        # INTERSECT ALL is part of the SQL standard but SQLite doesn't
        # implement it. We surface that as NotImplementedError at compile
        # time rather than letting the driver emit "syntax error near ALL".
        a = engine.create("bsi_a", {"x": int}, rows=[(1,), (1,), (2,)])
        b = engine.create("bsi_b", {"x": int}, rows=[(1,), (1,), (1,), (2,)])
        # Set semantics still works.
        assert sorted(r[0] for r in a.intersect(b).collect()) == [1, 2]
        # Bag mode raises with the dialect-specific guidance.
        with pytest.raises(NotImplementedError, match="INTERSECT ALL"):
            a.intersect(b).bags().collect()

    def test_bags_over_difference_raises_on_sqlite(self, engine):
        # Same story for EXCEPT ALL.
        a = engine.create("bsd_a", {"x": int}, rows=[(1,), (1,), (1,), (2,)])
        b = engine.create("bsd_b", {"x": int}, rows=[(1,)])
        assert sorted(r[0] for r in a.difference(b).collect()) == [2]
        with pytest.raises(NotImplementedError, match="EXCEPT ALL"):
            a.difference(b).bags().collect()


class TestSQLTypePrefixMatch:
    # INTERVAL starts with the literal SQL type "INT", and the previous
    # prefix-matcher returned the int mapping for it. The boundary check
    # blocks that while still letting parametric types like VARCHAR(255)
    # and the canonical INTEGER/INT spellings resolve correctly.
    def test_interval_does_not_match_int(self, engine):
        from datetime import datetime
        assert engine._sql_type_to_python("INTERVAL") is str
        # An unknown type still falls back to str, by design.
        assert engine._sql_type_to_python("ENUM") is str
        # Sanity: parametric and bare integer spellings still work.
        assert engine._sql_type_to_python("VARCHAR(255)") is str
        assert engine._sql_type_to_python("INTEGER") is int
        assert engine._sql_type_to_python("INT") is int
        assert engine._sql_type_to_python("INT(11)") is int
        # And a real timestamp type still resolves.
        assert engine._sql_type_to_python("TIMESTAMP") is datetime


class TestNestedSetOps:
    # Regression for chained set operations compiling without grouping.
    # SQL set operators have no precedence and associate left-to-right, so
    # bare compound SELECTs side by side flatten the algebra tree: e.g.
    # a.difference(b.difference(c)) would compile to ((a EXCEPT b) EXCEPT c)
    # and return the wrong rows. The compiler now subquery-wraps any operand
    # that is itself a set operation. Only test_except_right_nesting and
    # test_union_over_intersect_precedence go red on the unpatched compiler;
    # the rest are non-regression guards (left-nesting and bag-mode behavior
    # were already correct and must stay correct).

    def test_except_right_nesting(self, engine):
        # a-(b-c): b-c={2}, so a-{2}={1,3}. Pre-fix this flattened to
        # (a-b)-c={1} (SQLite-reproducible wrong result).
        a = engine.create("nse_a", {"x": int}, rows=[(1,), (2,), (3,)])
        b = engine.create("nse_b", {"x": int}, rows=[(2,), (3,), (4,)])
        c = engine.create("nse_c", {"x": int}, rows=[(3,), (4,), (5,)])
        expr = a.difference(b.difference(c))
        assert expr.algebra() == "(nse_a − (nse_b − nse_c))"
        assert sorted(r[0] for r in expr.collect()) == [1, 3]

    def test_except_left_nesting_unchanged(self, engine):
        # (a-b)-c: a-b={1}, {1}-c={1}. Left-nesting was already correct;
        # the fix must not regress it.
        a = engine.create("nsl_a", {"x": int}, rows=[(1,), (2,), (3,)])
        b = engine.create("nsl_b", {"x": int}, rows=[(2,), (3,), (4,)])
        c = engine.create("nsl_c", {"x": int}, rows=[(3,), (4,), (5,)])
        expr = a.difference(b).difference(c)
        assert sorted(r[0] for r in expr.collect()) == [1]

    def test_union_over_intersect_precedence(self, engine):
        # a UNION (b INTERSECT c): b∩c={3,4}, a∪{3,4}={1,2,3,4}. Pre-fix the
        # flattened (a UNION b) INTERSECT c gave {3,4}.
        a = engine.create("nui_a", {"x": int}, rows=[(1,), (2,), (3,)])
        b = engine.create("nui_b", {"x": int}, rows=[(2,), (3,), (4,)])
        c = engine.create("nui_c", {"x": int}, rows=[(3,), (4,), (5,)])
        expr = a.union(b.intersect(c))
        assert sorted(r[0] for r in expr.collect()) == [1, 2, 3, 4]

    def test_setop_left_operand_grouped(self, engine):
        # (a UNION b) - c with the set-op on the LEFT operand: a∪b={1,2,3},
        # minus c={3,4} -> {1,2}. Confirms left operands are wrapped too.
        a = engine.create("nlo_a", {"x": int}, rows=[(1,), (2,)])
        b = engine.create("nlo_b", {"x": int}, rows=[(2,), (3,)])
        c = engine.create("nlo_c", {"x": int}, rows=[(3,), (4,)])
        expr = a.union(b).difference(c)
        assert sorted(r[0] for r in expr.collect()) == [1, 2]

    def test_nested_union_all_preserves_bag(self, engine):
        # Nested UNION ALL in bag mode must keep duplicates: the wrapper
        # SELECT honors bag_mode (no DISTINCT) so the inner multiset survives.
        # d=[1,1,2], e=[2,3], f=[3,4]; d ∪ALL (e ∪ALL f) -> [1,1,2,2,3,3,4].
        d = engine.create("nua_d", {"x": int}, rows=[(1,), (1,), (2,)])
        e = engine.create("nua_e", {"x": int}, rows=[(2,), (3,)])
        f = engine.create("nua_f", {"x": int}, rows=[(3,), (4,)])
        rows = sorted(r[0] for r in d.union(e.union(f)).bags().collect())
        assert rows == [1, 1, 2, 2, 3, 3, 4]

    def test_nested_intersect_all_still_raises_on_sqlite(self, engine):
        # Bag semantics over nested INTERSECT must still surface the SQLite
        # INTERSECT ALL limitation (the fix does not change _setop_keyword).
        d = engine.create("nia_d", {"x": int}, rows=[(1,), (2,), (3,)])
        e = engine.create("nia_e", {"x": int}, rows=[(2,), (3,)])
        f = engine.create("nia_f", {"x": int}, rows=[(3,), (4,)])
        with pytest.raises(NotImplementedError, match="INTERSECT ALL"):
            d.intersect(e.intersect(f)).bags().collect()


class TestGroupingAggValidation:
    # Aggregate target attributes must be validated at construction, the
    # same as group keys. Pre-fix, an unknown agg attr was emitted as a
    # quoted identifier; SQLite reads it as a string literal and SUM/AVG of
    # it returns 0.0 with no error, so a typo produced silent wrong numbers.
    def test_bad_agg_attr_raises_at_construction(self, sp_data):
        s, p, sp, engine = sp_data
        with pytest.raises(AttributeError_):
            sp.group("sno", bad=sum_("nonexistent"))

    def test_bad_agg_attr_message(self, sp_data):
        s, p, sp, engine = sp_data
        with pytest.raises(AttributeError_) as exc:
            sp.group("sno", bad=sum_("nonexistent"))
        msg = str(exc.value)
        assert "nonexistent" in msg
        assert "sno" in msg and "pno" in msg and "qty" in msg

    def test_count_star_default_still_works(self, sp_data):
        # Guards the "*" exemption: count() with its default must still
        # construct and collect.
        s, p, sp, engine = sp_data
        result = sp.group(total=count()).collect()
        assert len(result) == 1
        assert result[0][0] == 12

    def test_real_agg_attrs_still_work(self, sp_data):
        # Guards that valid aggregate targets still validate and compute.
        s, p, sp, engine = sp_data
        result = sp.group("sno", n=count("pno"), t=sum_("qty")).collect()
        result_dict = {row[0]: (row[1], row[2]) for row in result}
        assert result_dict["S1"] == (6, 1300)
