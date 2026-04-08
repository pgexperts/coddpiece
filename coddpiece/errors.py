"""Educational error types for relational algebra operations."""


class RelationalError(Exception):
    """Base class for all relational algebra errors."""


class SchemaError(RelationalError):
    """Raised when schemas are incompatible for an operation.

    Includes details about both schemas, what's missing/extra,
    and a hint about how to fix the problem.
    """


class DomainError(RelationalError):
    """Raised when an attribute's type (domain) is unsupported or mismatched."""


# Trailing underscore follows PEP 8's convention for avoiding collisions
# with Python builtins (same convention as sum_, min_, max_ in aggregates.py).
class AttributeError_(RelationalError):
    """Raised when referencing an attribute that doesn't exist in a relation.

    Named with trailing underscore to avoid shadowing the builtin.
    """



class EngineError(RelationalError):
    """Raised when operations span different engines (different DB connections)."""


class PredicateError(RelationalError):
    """Raised when a predicate is constructed incorrectly."""
