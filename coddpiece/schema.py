"""Schema system for relational algebra.

A Schema is an ordered collection of named, typed Attributes.
It enforces the relational model's type discipline and provides
operations needed by the algebra: compatibility checks, composition,
projection, and renaming.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Iterator

from .errors import AttributeError_, DomainError, SchemaError

# Bidirectional type bridge between Python and SQL. SUPPORTED_DOMAINS is the
# canonical Python→SQL direction; Dialect subclasses may override the SQL side.
SUPPORTED_DOMAINS: dict[type, str] = {
    int: "INTEGER",
    float: "REAL",
    str: "TEXT",
    bool: "BOOLEAN",
    Decimal: "NUMERIC",
    date: "DATE",
    datetime: "TIMESTAMP",
}

# Reverse mapping: intentionally many-to-one (e.g. INT, BIGINT, SMALLINT → int)
# to absorb cross-database type name variation during schema introspection.
SQL_TO_PYTHON: dict[str, type] = {
    "INTEGER": int,
    "INT": int,
    "BIGINT": int,
    "SMALLINT": int,
    "REAL": float,
    "FLOAT": float,
    "DOUBLE": float,
    "DOUBLE PRECISION": float,
    "TEXT": str,
    "VARCHAR": str,
    "CHAR": str,
    "CHARACTER VARYING": str,
    "BOOLEAN": bool,
    "BOOL": bool,
    "NUMERIC": Decimal,
    "DECIMAL": Decimal,
    "DATE": date,
    "TIMESTAMP": datetime,
    "TIMESTAMP WITHOUT TIME ZONE": datetime,
    "TIMESTAMP WITH TIME ZONE": datetime,
}


@dataclass(frozen=True)
class Attribute:
    """A named attribute with a domain (type).

    Attributes are the columns of a relation. Each has a name
    (which must be a valid Python identifier) and a domain
    (a Python type representing the set of allowed values).
    """

    name: str
    domain: type

    def __post_init__(self):
        # Names must be valid Python identifiers because BaseRelation.__getattr__
        # exposes them as relation.column_name — enforced eagerly at construction.
        if not isinstance(self.name, str) or not self.name.isidentifier():
            raise AttributeError_(
                f"Invalid attribute name: {self.name!r}. "
                f"Attribute names must be valid Python identifiers."
            )
        if self.domain not in SUPPORTED_DOMAINS:
            supported = ", ".join(t.__name__ for t in SUPPORTED_DOMAINS)
            raise DomainError(
                f"Unsupported domain: {self.domain!r}. "
                f"Supported domains: {supported}"
            )

    def __repr__(self) -> str:
        return f"{self.name}: {self.domain.__name__}"


# frozen=True + tuple storage: schemas are shared across expression tree nodes,
# so immutability is essential — mutation would silently corrupt the tree.
@dataclass(frozen=True)
class Schema:
    """An ordered collection of named, typed attributes.

    Schemas define the structure of a relation. The relational model
    treats relations as sets of tuples over named attributes —
    attribute order is not semantically significant, but we preserve
    it for display consistency.
    """

    attributes: tuple[Attribute, ...]

    def __post_init__(self):
        names = [a.name for a in self.attributes]
        dupes = [n for n in names if names.count(n) > 1]
        if dupes:
            raise SchemaError(
                f"Duplicate attribute names: {set(dupes)}. "
                f"All attribute names in a schema must be unique."
            )

    # --- Accessors ---

    def names(self) -> tuple[str, ...]:
        """Attribute names in order."""
        return tuple(a.name for a in self.attributes)

    def domains(self) -> tuple[type, ...]:
        """Attribute domains in order."""
        return tuple(a.domain for a in self.attributes)

    def __getitem__(self, name: str) -> Attribute:
        """Look up an attribute by name."""
        # Linear scan is fine — schemas rarely exceed ~20 attributes, and a dict
        # would add complexity for no measurable gain.
        for attr in self.attributes:
            if attr.name == name:
                return attr
        raise AttributeError_(
            f"No attribute {name!r} in schema. "
            f"Available: {', '.join(self.names())}"
        )

    def __contains__(self, name: str) -> bool:
        return any(a.name == name for a in self.attributes)

    def __len__(self) -> int:
        return len(self.attributes)

    def __iter__(self) -> Iterator[Attribute]:
        return iter(self.attributes)

    def __repr__(self) -> str:
        attrs = ", ".join(repr(a) for a in self.attributes)
        return f"{{{attrs}}}"

    # --- Relational operations on schemas ---

    def compatible(self, other: Schema) -> bool:
        """True if both schemas have the same attribute names and domains.

        Required for UNION, INTERSECT, and DIFFERENCE.
        Order-independent: {a: int, b: str} is compatible with {b: str, a: int}.
        """
        if len(self) != len(other):
            return False
        # Set comparison: in relational theory, attribute order is irrelevant
        # for union-compatibility. Schema preserves order only for display.
        self_set = {(a.name, a.domain) for a in self.attributes}
        other_set = {(a.name, a.domain) for a in other.attributes}
        return self_set == other_set

    def common(self, other: Schema) -> Schema:
        """Attributes shared by both schemas (by name AND domain).

        Used for natural join to find the join columns.
        """
        other_map = {a.name: a.domain for a in other.attributes}
        shared = []
        for attr in self.attributes:
            if attr.name in other_map:
                # Deliberate strictness: same-named attributes must share a domain,
                # matching relational theory's constraint on attribute identity.
                if attr.domain != other_map[attr.name]:
                    raise DomainError(
                        f"Common attribute {attr.name!r} has different domains: "
                        f"{attr.domain.__name__} vs {other_map[attr.name].__name__}. "
                        f"Use RENAME to resolve the conflict."
                    )
                shared.append(attr)
        return Schema(tuple(shared))

    def compose(self, other: Schema) -> Schema:
        """Merge two schemas for cross product.

        Raises SchemaError if any attribute names collide,
        since the result would be ambiguous.
        """
        # Cross product requires fully disjoint names — any overlap would make
        # the result schema ambiguous. The error suggests RENAME as a fix.
        collisions = set(self.names()) & set(other.names())
        if collisions:
            raise SchemaError(
                f"CROSS PRODUCT requires disjoint attribute names. "
                f"Collision: {collisions}. "
                f"Hint: Use RENAME on one relation first to disambiguate."
            )
        return Schema(self.attributes + other.attributes)

    def join_compose(self, other: Schema) -> Schema:
        """Merge two schemas for natural join.

        Common attributes appear once (from the left).
        Remaining right attributes are appended.
        """
        # Standard natural join rule: common attributes come from the LEFT side
        # only (appear once, not twice), then non-common right attrs are appended.
        common_names = {a.name for a in self.common(other).attributes}
        right_only = tuple(
            a for a in other.attributes if a.name not in common_names
        )
        return Schema(self.attributes + right_only)

    def project(self, *names: str) -> Schema:
        """Subschema with only the named attributes, preserving order."""
        unknown = set(names) - set(self.names())
        if unknown:
            raise AttributeError_(
                f"Cannot PROJECT on unknown attributes: {unknown}. "
                f"Available: {', '.join(self.names())}"
            )
        attrs = tuple(self[n] for n in names)
        return Schema(attrs)

    def rename(self, **mapping: str) -> Schema:
        """New schema with attributes renamed per mapping.

        mapping is {new_name: old_name}.
        """
        # Convention: mapping is {new_name: old_name}, which looks backwards but
        # matches the relation.rename(new='old') kwarg syntax where the keyword
        # IS the new name.
        for new_name, old_name in mapping.items():
            if old_name not in self:
                raise AttributeError_(
                    f"Cannot RENAME: no attribute {old_name!r} in schema. "
                    f"Available: {', '.join(self.names())}"
                )
            if not new_name.isidentifier():
                raise AttributeError_(
                    f"Invalid new attribute name: {new_name!r}. "
                    f"Attribute names must be valid Python identifiers."
                )

        # Invert to old→new for iteration over the original attribute list.
        reverse = {old: new for new, old in mapping.items()}
        attrs = tuple(
            Attribute(reverse.get(a.name, a.name), a.domain)
            for a in self.attributes
        )
        return Schema(attrs)

    def subtract(self, other: Schema) -> Schema:
        """Attributes in self but not in other (by name). Used only by Division
        to compute the result schema (dividend attrs minus divisor attrs)."""
        other_names = set(other.names())
        return Schema(tuple(a for a in self.attributes if a.name not in other_names))

    # --- Helpers for error messages ---

    def diff(self, other: Schema) -> str:
        """Human-readable diff — diagnostic only, used in error messages,
        not a relational operation."""
        self_names = set(self.names())
        other_names = set(other.names())
        only_left = self_names - other_names
        only_right = other_names - self_names
        common = self_names & other_names

        # Check domain mismatches in common attributes
        domain_mismatches = []
        for name in common:
            if self[name].domain != other[name].domain:
                domain_mismatches.append(
                    f"  {name}: {self[name].domain.__name__} vs "
                    f"{other[name].domain.__name__}"
                )

        lines = []
        if only_left:
            lines.append(f"  Only in left:  {', '.join(sorted(only_left))}")
        if only_right:
            lines.append(f"  Only in right: {', '.join(sorted(only_right))}")
        if domain_mismatches:
            lines.append(f"  Domain mismatches:")
            lines.extend(domain_mismatches)
        if common and not domain_mismatches:
            lines.append(f"  Common:        {', '.join(sorted(common))}")
        return "\n".join(lines)
