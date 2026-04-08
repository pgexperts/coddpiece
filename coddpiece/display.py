"""Display renderers for relational algebra expressions.

Renders expressions as:
- Algebra notation (σ, π, ⋈, etc.)
- Indented tree with box-drawing characters
- Natural-language gloss
- Composite .explain() view
"""

from __future__ import annotations

from .relation import (
    Antijoin,
    BaseRelation,
    CrossProduct,
    Difference,
    Division,
    Equijoin,
    Grouping,
    Intersect,
    NaturalJoin,
    OuterJoin,
    Projection,
    Relation,
    Rename,
    Selection,
    Semijoin,
    ThetaJoin,
    Union,
)


# ---------------------------------------------------------------------------
# Algebra notation
# ---------------------------------------------------------------------------

def render_algebra(node: BaseRelation) -> str:
    """Render an expression as standard relational algebra notation."""
    # Recursive isinstance dispatch. Each node type is distinct, so check
    # ordering doesn't matter. Returns Unicode algebra symbols (σ, π, ⋈, etc.).
    if isinstance(node, Relation):
        return node.relation_name

    if isinstance(node, Selection):
        return f"σ({node.predicate.algebra()})({render_algebra(node.child)})"

    if isinstance(node, Projection):
        attrs = ",".join(node.attrs)
        return f"π({attrs})({render_algebra(node.child)})"

    if isinstance(node, Rename):
        pairs = ",".join(
            f"{new}/{old}" for new, old in node.mapping.items()
        )
        return f"ρ({pairs})({render_algebra(node.child)})"

    if isinstance(node, CrossProduct):
        return f"({render_algebra(node.left)} × {render_algebra(node.right)})"

    if isinstance(node, NaturalJoin):
        return f"({render_algebra(node.left)} ⋈ {render_algebra(node.right)})"

    if isinstance(node, ThetaJoin):
        return (
            f"({render_algebra(node.left)} "
            f"⋈({node.predicate.algebra()}) "
            f"{render_algebra(node.right)})"
        )

    if isinstance(node, Equijoin):
        return (
            f"({render_algebra(node.left)} "
            f"⋈({node.left_attr}={node.right_attr}) "
            f"{render_algebra(node.right)})"
        )

    if isinstance(node, Semijoin):
        return f"({render_algebra(node.left)} ⋉ {render_algebra(node.right)})"

    if isinstance(node, Antijoin):
        return f"({render_algebra(node.left)} ▷ {render_algebra(node.right)})"

    if isinstance(node, OuterJoin):
        sym = {"left": "⟕", "right": "⟖", "full": "⟗"}[node.how]
        return f"({render_algebra(node.left)} {sym} {render_algebra(node.right)})"

    if isinstance(node, Union):
        return f"({render_algebra(node.left)} ∪ {render_algebra(node.right)})"

    if isinstance(node, Intersect):
        return f"({render_algebra(node.left)} ∩ {render_algebra(node.right)})"

    if isinstance(node, Difference):
        return f"({render_algebra(node.left)} − {render_algebra(node.right)})"

    if isinstance(node, Division):
        return f"({render_algebra(node.left)} ÷ {render_algebra(node.right)})"

    if isinstance(node, Grouping):
        keys = ",".join(node.keys) if node.keys else ""
        aggs = ",".join(
            f"{name}←{agg.algebra()}" for name, agg in node.aggs.items()
        )
        spec = f"{keys}; {aggs}" if keys else aggs
        return f"γ({spec})({render_algebra(node.child)})"

    return f"<{type(node).__name__}>"


# ---------------------------------------------------------------------------
# Tree renderer
# ---------------------------------------------------------------------------

def render_tree(node: BaseRelation, prefix: str = "", is_last: bool = True) -> str:
    """Render an expression as an indented tree with box-drawing characters."""
    lines: list[str] = []
    _tree_walk(node, lines, "", "")
    return "\n".join(lines)


def _tree_walk(
    node: BaseRelation,
    lines: list[str],
    prefix: str,
    connector: str,
) -> None:
    label = _node_label(node)
    lines.append(f"{prefix}{connector}{label}")

    children = _get_children(node)
    # The continuation prefix for this node's children: "│" if more siblings
    # follow (├─), spaces if last child (└─). Standard box-drawing tree algorithm.
    if connector == "":
        extension = ""
    elif connector == "├─ ":
        extension = "│  "
    else:  # "└─ "
        extension = "   "
    child_prefix = prefix + extension

    for i, child in enumerate(children):
        is_last = i == len(children) - 1
        child_connector = "└─ " if is_last else "├─ "
        _tree_walk(child, lines, child_prefix, child_connector)


def _node_label(node: BaseRelation) -> str:
    if isinstance(node, Relation):
        return node.relation_name

    if isinstance(node, Selection):
        return f"Selection({node.predicate.algebra()})"

    if isinstance(node, Projection):
        return f"Project({', '.join(node.attrs)})"

    if isinstance(node, Rename):
        pairs = ", ".join(f"{new}←{old}" for new, old in node.mapping.items())
        return f"Rename({pairs})"

    if isinstance(node, CrossProduct):
        return "CrossProduct"

    if isinstance(node, NaturalJoin):
        common = node.left._schema().common(node.right._schema())
        return f"NaturalJoin(on: {', '.join(common.names())})"

    if isinstance(node, ThetaJoin):
        return f"ThetaJoin({node.predicate.algebra()})"

    if isinstance(node, Equijoin):
        return f"Equijoin({node.left_attr}={node.right_attr})"

    if isinstance(node, Semijoin):
        return "Semijoin"

    if isinstance(node, Antijoin):
        return "Antijoin"

    if isinstance(node, OuterJoin):
        return f"OuterJoin({node.how})"

    if isinstance(node, Union):
        return "Union"

    if isinstance(node, Intersect):
        return "Intersect"

    if isinstance(node, Difference):
        return "Difference"

    if isinstance(node, Division):
        return "Division"

    if isinstance(node, Grouping):
        keys = ", ".join(node.keys) if node.keys else "∅"
        aggs = ", ".join(
            f"{name}←{agg.algebra()}" for name, agg in node.aggs.items()
        )
        return f"Group(by: {keys}; {aggs})"

    return type(node).__name__


def _get_children(node: BaseRelation) -> list[BaseRelation]:
    if isinstance(node, Relation):
        return []
    if isinstance(node, (Selection, Projection, Rename, Grouping)):
        return [node.child]
    # Pragmatic shortcut: all binary nodes (joins, set ops, division) have
    # left/right attributes, so hasattr avoids listing every binary type.
    if hasattr(node, "left") and hasattr(node, "right"):
        return [node.left, node.right]
    return []


# ---------------------------------------------------------------------------
# Natural-language gloss
# ---------------------------------------------------------------------------

def render_gloss(node: BaseRelation) -> str:
    """Render a natural-language reading of the expression."""
    lines: list[str] = []
    _gloss_walk(node, lines)
    return " ".join(lines)


def _gloss_walk(node: BaseRelation, lines: list[str]) -> None:
    # Bottom-up traversal: children processed first, then the current node.
    # Relation leaves are skipped during child recursion but emit "Start with X"
    # when encountered directly, producing natural reading order.
    if isinstance(node, Relation):
        lines.append(f"Start with {node.relation_name}.")
        return

    children = _get_children(node)
    for child in children:
        if isinstance(child, Relation):
            continue
        _gloss_walk(child, lines)

    if isinstance(node, Selection):
        lines.append(f"Keep only rows where {node.predicate.algebra()}.")

    elif isinstance(node, Projection):
        lines.append(f"Keep only columns {', '.join(node.attrs)}.")

    elif isinstance(node, Rename):
        pairs = ", ".join(f"{old} → {new}" for new, old in node.mapping.items())
        lines.append(f"Rename {pairs}.")

    elif isinstance(node, CrossProduct):
        lines.append(
            f"Take the Cartesian product of {node.left.relation_name} "
            f"and {node.right.relation_name}."
        )

    elif isinstance(node, NaturalJoin):
        common = node.left._schema().common(node.right._schema())
        lines.append(
            f"Combine {node.left.relation_name} and {node.right.relation_name} "
            f"matching on {', '.join(common.names())}."
        )

    elif isinstance(node, ThetaJoin):
        lines.append(
            f"Join {node.left.relation_name} and {node.right.relation_name} "
            f"where {node.predicate.algebra()}."
        )

    elif isinstance(node, Equijoin):
        lines.append(
            f"Join {node.left.relation_name} and {node.right.relation_name} "
            f"where {node.left_attr} = {node.right_attr}."
        )

    elif isinstance(node, Semijoin):
        lines.append(
            f"Keep {node.left.relation_name} rows that have a match "
            f"in {node.right.relation_name}."
        )

    elif isinstance(node, Antijoin):
        lines.append(
            f"Keep {node.left.relation_name} rows that have NO match "
            f"in {node.right.relation_name}."
        )

    elif isinstance(node, OuterJoin):
        lines.append(
            f"{node.how.title()} outer join {node.left.relation_name} "
            f"and {node.right.relation_name}, preserving unmatched rows."
        )

    elif isinstance(node, Union):
        lines.append(
            f"Combine all tuples from {node.left.relation_name} "
            f"and {node.right.relation_name}."
        )

    elif isinstance(node, Intersect):
        lines.append(
            f"Keep tuples appearing in both {node.left.relation_name} "
            f"and {node.right.relation_name}."
        )

    elif isinstance(node, Difference):
        lines.append(
            f"Keep tuples in {node.left.relation_name} "
            f"that are not in {node.right.relation_name}."
        )

    elif isinstance(node, Division):
        result_attrs = node._schema().names()
        divisor_attrs = node.right._schema().names()
        lines.append(
            f"Find {', '.join(result_attrs)} associated with "
            f"ALL {', '.join(divisor_attrs)} in {node.right.relation_name}."
        )

    elif isinstance(node, Grouping):
        keys = ", ".join(node.keys) if node.keys else "the entire relation"
        aggs = ", ".join(
            f"{agg.algebra()} as {name}" for name, agg in node.aggs.items()
        )
        lines.append(f"Group by {keys}, computing {aggs}.")


# ---------------------------------------------------------------------------
# Composite explain
# ---------------------------------------------------------------------------

def render_explain(node: BaseRelation) -> str:
    """Side-by-side algebra, tree, SQL, and natural-language reading."""
    from .compiler import Compiler

    algebra = render_algebra(node)
    tree = render_tree(node)

    compiler = Compiler(node._engine.dialect)
    sql, params = compiler.compile(node)
    sql = format_sql(sql)
    if params:
        sql += f"\n-- params: {params}"

    gloss = render_gloss(node)

    sections = [
        ("Algebra", algebra),
        ("Tree", tree),
        ("SQL", sql),
        ("Reading", gloss),
    ]

    parts = []
    for title, body in sections:
        indented = "\n".join(f"  {line}" for line in body.split("\n"))
        parts.append(f"{title}:\n{indented}")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Table renderer (for __str__)
# ---------------------------------------------------------------------------

def render_table(node: BaseRelation, max_rows: int = 50) -> str:
    """Render a relation as a formatted ASCII table."""
    schema = node._schema()
    headers = list(schema.names())
    rows = node.collect()

    truncated = len(rows) > max_rows
    display_rows = rows[:max_rows]

    # Compute column widths
    str_rows = [[str(v) for v in row] for row in display_rows]
    widths = [len(h) for h in headers]
    for row in str_rows:
        for i, val in enumerate(row):
            # Guard handles rows with more values than headers — defensive,
            # shouldn't happen with correct schemas.
            if i < len(widths):
                widths[i] = max(widths[i], len(val))

    def fmt_row(values: list[str]) -> str:
        cells = [v.ljust(widths[i]) for i, v in enumerate(values)]
        return "│ " + " │ ".join(cells) + " │"

    sep_top = "┌─" + "─┬─".join("─" * w for w in widths) + "─┐"
    sep_mid = "├─" + "─┼─".join("─" * w for w in widths) + "─┤"
    sep_bot = "└─" + "─┴─".join("─" * w for w in widths) + "─┘"

    lines = [sep_top, fmt_row(headers), sep_mid]
    for row in str_rows:
        lines.append(fmt_row(row))
    lines.append(sep_bot)

    count_line = f"({len(rows)} row{'s' if len(rows) != 1 else ''})"
    if truncated:
        count_line = f"({len(rows)} rows, showing first {max_rows})"
    lines.append(count_line)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SQL formatter
# ---------------------------------------------------------------------------


def format_sql(sql: str) -> str:
    """Format SQL for readability with indentation."""
    # Simple regex-based formatter. The paren-counting indenter is approximate —
    # it doesn't parse SQL, just counts ( vs ) per line. Works well enough for
    # the compiler's relatively regular output.
    import re

    formatted = sql
    for kw in ["FROM", "WHERE", "JOIN", "LEFT OUTER JOIN", "RIGHT OUTER JOIN",
               "FULL OUTER JOIN", "GROUP BY", "HAVING", "ORDER BY",
               "UNION", "INTERSECT", "EXCEPT"]:
        # Only break before top-level clauses (not inside subqueries)
        formatted = re.sub(
            rf'\s+({kw})\b',
            rf'\n\1',
            formatted,
        )

    # Indent subqueries
    lines = formatted.split("\n")
    result = []
    indent = 0
    for line in lines:
        stripped = line.strip()
        # Count parens to track nesting
        opens = stripped.count("(")
        closes = stripped.count(")")
        if closes > opens:
            indent = max(0, indent - (closes - opens))
        result.append("  " * indent + stripped)
        if opens > closes:
            indent += opens - closes

    return "\n".join(result)
