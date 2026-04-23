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
    Engine, Schema, Attribute, count, sum_, avg, min_, max_,
    SchemaError, DomainError, PredicateError,
)
from coddpiece.errors import AttributeError_
from coddpiece.predicates import Attr, Predicate


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
            (s.city == "London") and (s.status > 10)


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
