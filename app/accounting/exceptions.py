from __future__ import annotations


class UnbalancedEntryError(Exception):
    """Raised when a journal entry's debits do not equal its credits."""


class EmptyEntryError(Exception):
    """Raised when a journal entry has no lines or a zero total."""


class VoidedEntryError(Exception):
    """Raised when an operation is attempted on an already-voided entry."""
