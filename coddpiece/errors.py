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
#
# Multiple inheritance from the builtin AttributeError is load-bearing:
# Python's hasattr() and three-argument getattr() special-case the builtin
# AttributeError alone. Without AttributeError in the MRO, BaseRelation's
# __getattr__ would propagate this exception out of hasattr/getattr and
# break @cached_property, IDE introspection, pickling, and any caller that
# uses hasattr() to test for column existence.
class AttributeError_(RelationalError, AttributeError):
    """Raised when referencing an attribute that doesn't exist in a relation.

    Named with trailing underscore to avoid shadowing the builtin.
    Inherits from AttributeError so hasattr/getattr behave correctly.
    """



class EngineError(RelationalError):
    """Raised when operations span different engines (different DB connections)."""


class PredicateError(RelationalError):
    """Raised when a predicate is constructed incorrectly."""
