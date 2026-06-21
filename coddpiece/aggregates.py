"""Aggregate function definitions for grouping operations.

Usage:
    from coddpiece import count, sum_, avg, min_, max_
    employees.group("department", headcount=count("name"), avg_sal=avg("salary"))
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schema import Schema


@dataclass(frozen=True)
class AggSpec:
    """Specification for an aggregate function."""

    func: str   # COUNT, SUM, AVG, MIN, MAX
    attr: str   # Attribute to aggregate, or "*" for COUNT(*)

    def output_domain(self, schema: Schema) -> type:
        """Infer the output type of this aggregate."""
        # COUNT always returns int; AVG always returns float (even for integer
        # inputs — matches SQL behavior). SUM/MIN/MAX preserve the input domain.
        if self.func == "COUNT":
            return int
        if self.func == "AVG":
            return float
        # SUM, MIN, MAX preserve input domain
        # attr == "*" only makes sense for COUNT, but guard defensively.
        if self.attr == "*":
            return int
        # Cross-file invariant: the only caller, Grouping._schema(), runs
        # after Grouping.__post_init__ has already verified each non-"*"
        # aggregate attr exists on the child schema, so this lookup cannot
        # raise here in normal flow. (Calling output_domain directly on an
        # AggSpec with a bogus attr would still raise AttributeError_.)
        return schema[self.attr].domain

    def algebra(self) -> str:
        return f"{self.func}({self.attr})"


def count(attr: str = "*") -> AggSpec:
    """COUNT aggregate."""
    return AggSpec("COUNT", attr)


# Trailing underscores on sum_, min_, max_ follow PEP 8's convention
# for avoiding name collisions with Python builtins.

def sum_(attr: str) -> AggSpec:
    """SUM aggregate."""
    return AggSpec("SUM", attr)


def avg(attr: str) -> AggSpec:
    """AVG aggregate."""
    return AggSpec("AVG", attr)


def min_(attr: str) -> AggSpec:
    """MIN aggregate."""
    return AggSpec("MIN", attr)


def max_(attr: str) -> AggSpec:
    """MAX aggregate."""
    return AggSpec("MAX", attr)
