"""Predicate system for relational algebra selection.

When you write `employees.salary > 50000`, Python calls
`Attr.__gt__(Literal(50000))` which returns a Predicate node.
The predicate tree can be rendered as algebra notation or compiled to SQL.

Important: Python's `and`, `or`, `not` keywords cannot be overridden.
Use `&`, `|`, `~` instead. If you accidentally use `and`, the Predicate's
`__bool__` will raise a helpful error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .errors import PredicateError

if TYPE_CHECKING:
    from .relation import BaseRelation


@dataclass(frozen=True)
class Literal:
    """A constant value in a predicate (e.g., 50000, 'London')."""

    # Wrapping raw Python values in a Literal node (rather than leaving them
    # bare) gives the compiler a uniform Attr|Literal dispatch: Attr -> column
    # reference, Literal -> `?` placeholder + appended param. This is what
    # makes the "literals never interpolated" SQL-safety invariant easy to
    # enforce in compiler.py and Predicate.sql().
    value: Any

    def __repr__(self) -> str:
        if isinstance(self.value, str):
            return f'"{self.value}"'
        return repr(self.value)


@dataclass(frozen=True)
class Attr:
    """Proxy for a relation attribute.

    Instead of evaluating comparisons, captures them as Predicate nodes.
    This is the key trick that lets us write Pythonic predicates that
    compile to both algebra notation and SQL.
    """

    source: BaseRelation
    name: str

    # __eq__ and __ne__ return Predicate instead of bool, deliberately breaking
    # Liskov substitution (object.__eq__ returns bool). This is the core trick:
    # hijacking Python's comparison operators to build an expression tree.
    # The type: ignore[override] silences mypy's rightful complaint.
    def __eq__(self, other: Any) -> Predicate:  # type: ignore[override]
        return Predicate("=", self, _wrap(other))

    def __ne__(self, other: Any) -> Predicate:  # type: ignore[override]
        return Predicate("!=", self, _wrap(other))

    def __lt__(self, other: Any) -> Predicate:
        return Predicate("<", self, _wrap(other))

    def __le__(self, other: Any) -> Predicate:
        return Predicate("<=", self, _wrap(other))

    def __gt__(self, other: Any) -> Predicate:
        return Predicate(">", self, _wrap(other))

    def __ge__(self, other: Any) -> Predicate:
        return Predicate(">=", self, _wrap(other))

    def __repr__(self) -> str:
        return f"{self.source.relation_name}.{self.name}"

    # Since __eq__ returns Predicate (not bool), the default hash is broken.
    # We use id(self.source) for identity-based hashing: two Attr objects are
    # only hash-equal if they reference the exact same relation instance. This
    # is correct for the expression tree where identity, not structural equality,
    # determines equivalence.
    def __hash__(self) -> int:
        return hash((id(self.source), self.name))

    # Trap: Python calls __bool__ when `and`/`or`/`not` keywords are used.
    # Those keywords can't be overridden, so the best we can do is detect the
    # mistake and raise a helpful error pointing users to `&`/`|`/`~` instead.
    def __bool__(self) -> bool:
        raise PredicateError(
            "Cannot use Attr in a boolean context. "
            "If you're trying to use 'and'/'or'/'not', "
            "use '&', '|', '~' instead.\n"
            "  Wrong: employees.salary > 50000 and employees.dept == 'Eng'\n"
            "  Right: (employees.salary > 50000) & (employees.dept == 'Eng')"
        )


# Two parallel mappings from the same operator keys. ALGEBRA_SYMBOLS uses
# Unicode mathematical symbols (≠, ≤, ≥, ∧, ∨, ¬) for textbook-style display.
# SQL_OPERATORS maps to standard SQL syntax. Note `!=` maps to `<>` in SQL.
# Both dicts must stay in sync — any new operator needs entries in both.
ALGEBRA_SYMBOLS = {
    "=": "=",
    "!=": "≠",
    "<": "<",
    "<=": "≤",
    ">": ">",
    ">=": "≥",
    "AND": "∧",
    "OR": "∨",
    "NOT": "¬",
}

SQL_OPERATORS = {
    "=": "=",
    "!=": "<>",
    "<": "<",
    "<=": "<=",
    ">": ">",
    ">=": ">=",
    "AND": "AND",
    "OR": "OR",
    "NOT": "NOT",
}


@dataclass(frozen=True)
class Predicate:
    """A binary comparison predicate (e.g., salary > 50000)."""

    # Leaves of the predicate tree are always Attr or Literal — never a nested
    # Predicate. Logical composition lives in CompoundPredicate/NotPredicate,
    # keeping the comparison node shape simple and easy to compile.
    op: str
    left: Attr | Literal
    right: Attr | Literal

    # __and__, __or__, __invert__ form a closed algebra: combining any predicate
    # types always produces another predicate type (CompoundPredicate or
    # NotPredicate). This lets users chain arbitrarily: (p1 & p2) | ~p3.
    def __and__(self, other: Predicate | CompoundPredicate) -> CompoundPredicate:
        return CompoundPredicate("AND", self, other)

    def __or__(self, other: Predicate | CompoundPredicate) -> CompoundPredicate:
        return CompoundPredicate("OR", self, other)

    def __invert__(self) -> NotPredicate:
        return NotPredicate(self)

    # Every predicate class needs its own __bool__ trap because Python might
    # call bool() on any of them when used with `and`/`or`/`not` keywords.
    def __bool__(self) -> bool:
        raise PredicateError(
            "Cannot use a Predicate in a boolean context. "
            "Use '&' for AND, '|' for OR, '~' for NOT.\n"
            "  Wrong: pred1 and pred2\n"
            "  Right: pred1 & pred2"
        )

    def algebra(self) -> str:
        """Render as relational algebra notation."""
        # Attr renders as the bare column name (not qualified with the
        # relation), matching textbook σ-notation. Literals fall through to
        # Literal.__repr__ which quotes strings and repr's everything else.
        left = self.left.name if isinstance(self.left, Attr) else repr(self.left)
        right = self.right.name if isinstance(self.right, Attr) else repr(self.right)
        sym = ALGEBRA_SYMBOLS[self.op]
        return f"{left}{sym}{right}"

    def sql(self, dialect: Any = None) -> tuple[str, list]:
        """Render as SQL fragment with parameters."""
        # Returns (sql_string, param_values). Attr operands become column names
        # in the SQL string; Literal operands become `?` placeholders with
        # values appended to the params list. This prevents SQL injection.
        # The left-then-right append order must match the `?` order in the string.
        params: list = []

        if isinstance(self.left, Attr):
            left_sql = self.left.name
        else:
            left_sql = "?"
            params.append(self.left.value)

        if isinstance(self.right, Attr):
            right_sql = self.right.name
        else:
            right_sql = "?"
            params.append(self.right.value)

        sql_op = SQL_OPERATORS[self.op]
        return f"{left_sql} {sql_op} {right_sql}", params

    def __repr__(self) -> str:
        return self.algebra()


@dataclass(frozen=True)
class CompoundPredicate:
    """Logical combination of predicates (AND / OR)."""

    op: str  # "AND" or "OR"
    left: Predicate | CompoundPredicate | NotPredicate
    right: Predicate | CompoundPredicate | NotPredicate

    # Same closed-algebra operators as Predicate — see comment there.
    def __and__(self, other: Predicate | CompoundPredicate) -> CompoundPredicate:
        return CompoundPredicate("AND", self, other)

    def __or__(self, other: Predicate | CompoundPredicate) -> CompoundPredicate:
        return CompoundPredicate("OR", self, other)

    def __invert__(self) -> NotPredicate:
        return NotPredicate(self)

    # __bool__ trap repeated here — see Predicate.__bool__ for rationale.
    def __bool__(self) -> bool:
        raise PredicateError(
            "Cannot use a CompoundPredicate in a boolean context. "
            "Use '&' for AND, '|' for OR, '~' for NOT."
        )

    def algebra(self) -> str:
        sym = ALGEBRA_SYMBOLS[self.op]
        return f"({self.left.algebra()} {sym} {self.right.algebra()})"

    def sql(self, dialect: Any = None) -> tuple[str, list]:
        left_sql, left_params = self.left.sql(dialect)
        right_sql, right_params = self.right.sql(dialect)
        sql_op = SQL_OPERATORS[self.op]
        # Params are concatenated left-before-right, matching the order of `?`
        # placeholders in the generated SQL string. This ordering invariant is
        # critical — swapping would bind values to the wrong placeholders.
        return f"({left_sql} {sql_op} {right_sql})", left_params + right_params

    def __repr__(self) -> str:
        return self.algebra()


@dataclass(frozen=True)
class NotPredicate:
    """Logical negation of a predicate."""

    operand: Predicate | CompoundPredicate | NotPredicate

    # Same closed-algebra operators as Predicate — see comment there.
    def __and__(self, other: Predicate | CompoundPredicate) -> CompoundPredicate:
        return CompoundPredicate("AND", self, other)

    def __or__(self, other: Predicate | CompoundPredicate) -> CompoundPredicate:
        return CompoundPredicate("OR", self, other)

    # Double negation is allowed (~~p) — no simplification is attempted here;
    # the compiler or optimizer can handle that if needed.
    def __invert__(self) -> NotPredicate:
        return NotPredicate(self)

    # __bool__ trap repeated here — see Predicate.__bool__ for rationale.
    def __bool__(self) -> bool:
        raise PredicateError(
            "Cannot use a NotPredicate in a boolean context. "
            "Use '&' for AND, '|' for OR, '~' for NOT."
        )

    def algebra(self) -> str:
        return f"¬({self.operand.algebra()})"

    def sql(self, dialect: Any = None) -> tuple[str, list]:
        # Always parenthesize the inner fragment — NOT has higher precedence
        # than AND/OR in SQL, so `NOT a AND b` would bind wrong. Parens make
        # the generated SQL precedence-safe regardless of what `operand` is.
        inner_sql, params = self.operand.sql(dialect)
        return f"NOT ({inner_sql})", params

    def __repr__(self) -> str:
        return self.algebra()


# Type alias for any predicate node
PredicateNode = Predicate | CompoundPredicate | NotPredicate


def _wrap(value: Any) -> Attr | Literal:
    """Wrap a raw Python value as a Literal, or pass through Attr objects."""
    # Coerces raw Python values to Literal nodes so users can write
    # `r.salary > 50000` instead of `r.salary > Literal(50000)`.
    # Attr objects pass through unchanged, enabling attribute-to-attribute
    # comparisons like `r.salary > r.bonus`.
    if isinstance(value, Attr):
        return value
    return Literal(value)
