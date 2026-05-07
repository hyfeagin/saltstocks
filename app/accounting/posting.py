from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Callable, Optional

from app.accounting.exceptions import (
    EmptyEntryError,
    UnbalancedEntryError,
    VoidedEntryError,
)


@dataclass
class LineItem:
    """A single inventory purchase line used for freight-in allocation."""
    subtotal_cost: Decimal
    quantity: int
    inventory_item_id: Optional[int] = None
    memo: Optional[str] = None
    total_cost: Decimal = field(init=False)

    def __post_init__(self) -> None:
        self.total_cost = self.subtotal_cost


@dataclass
class JournalLineInput:
    account_id: int
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    memo: Optional[str] = None
    inventory_item_id: Optional[int] = None


@dataclass
class PostEntryRequest:
    entry_date: date
    description: str
    template_id: str
    total_amount: Decimal
    lines: list[JournalLineInput]
    created_by_method: str = "questionnaire"
    vendor: Optional[str] = None
    notes: Optional[str] = None


def _validate_post_entry(req: PostEntryRequest) -> None:
    """Validate a balanced journal entry request before writing it."""
    total_debits = sum(ln.debit for ln in req.lines)
    total_credits = sum(ln.credit for ln in req.lines)

    if total_debits == Decimal("0") and total_credits == Decimal("0"):
        raise EmptyEntryError("Entry has no lines or a zero total.")

    if total_debits != total_credits:
        raise UnbalancedEntryError(
            f"Debits ({total_debits}) do not equal credits ({total_credits})."
        )


def _insert_post_entry(conn: sqlite3.Connection, req: PostEntryRequest) -> int:
    """Insert a validated journal entry and its lines without opening a transaction."""
    cur = conn.execute(
        """
        INSERT INTO journal_entries
          (entry_date, description, template_id, total_amount,
           created_by_method, vendor, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            req.entry_date.isoformat(),
            req.description,
            req.template_id,
            str(req.total_amount),
            req.created_by_method,
            req.vendor,
            req.notes,
        ),
    )
    entry_id = cur.lastrowid

    conn.executemany(
        """
        INSERT INTO journal_lines
          (entry_id, account_id, debit, credit, memo, inventory_item_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                entry_id,
                ln.account_id,
                str(ln.debit),
                str(ln.credit),
                ln.memo,
                ln.inventory_item_id,
            )
            for ln in req.lines
        ],
    )
    return entry_id


def post_entry(conn: sqlite3.Connection, req: PostEntryRequest) -> int:
    """Validate and write a balanced journal entry atomically.

    Returns the new journal_entries.id.
    Raises EmptyEntryError if all amounts are zero.
    Raises UnbalancedEntryError if debits != credits.
    """
    return post_entries(conn, [req])[0]


def post_entries(
    conn: sqlite3.Connection,
    requests: list[PostEntryRequest],
    before_insert: Optional[Callable[[sqlite3.Connection], None]] = None,
    after_insert: Optional[Callable[[sqlite3.Connection, list[int]], None]] = None,
) -> list[int]:
    """Validate and write multiple balanced entries in one transaction."""
    if not requests:
        return []

    for req in requests:
        _validate_post_entry(req)

    with conn:
        if before_insert is not None:
            before_insert(conn)
        entry_ids = [_insert_post_entry(conn, req) for req in requests]
        if after_insert is not None:
            after_insert(conn, entry_ids)
    return entry_ids


def void_entry(conn: sqlite3.Connection, entry_id: int, reason: str) -> int:
    """Void an entry by creating a reversing entry (debits ↔ credits).

    Both the original and the reversing entry are marked is_void=True.
    Returns the new reversing entry's id.
    Raises VoidedEntryError if the entry is already voided.
    """
    row = conn.execute(
        """
        SELECT id, entry_date, description, template_id, total_amount,
               created_by_method, vendor, notes, is_void
        FROM journal_entries WHERE id = ?
        """,
        (entry_id,),
    ).fetchone()

    if row is None:
        raise ValueError(f"Journal entry {entry_id} not found.")

    if row[8]:
        raise VoidedEntryError(f"Entry {entry_id} is already voided.")

    lines = conn.execute(
        """
        SELECT account_id, debit, credit, memo, inventory_item_id
        FROM journal_lines WHERE entry_id = ?
        """,
        (entry_id,),
    ).fetchall()

    void_at = datetime.utcnow().isoformat()

    with conn:
        cur = conn.execute(
            """
            INSERT INTO journal_entries
              (entry_date, description, template_id, total_amount,
               created_by_method, vendor, notes, is_void, void_reason, void_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                row[1],
                f"VOID: {row[2]}",
                row[3],
                row[4],
                row[5],
                row[6],
                row[7],
                reason,
                void_at,
            ),
        )
        reversing_id = cur.lastrowid

        # Reversing entry swaps debit and credit on every line
        conn.executemany(
            """
            INSERT INTO journal_lines
              (entry_id, account_id, debit, credit, memo, inventory_item_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (reversing_id, ln[0], ln[2], ln[1], ln[3], ln[4])
                for ln in lines
            ],
        )

        conn.execute(
            """
            UPDATE journal_entries
            SET is_void = 1, void_reason = ?, void_at = ?
            WHERE id = ?
            """,
            (reason, void_at, entry_id),
        )

    return reversing_id


def allocate_freight_in(
    line_items: list[LineItem],
    freight_amount: Decimal,
) -> list[LineItem]:
    """Distribute freight_amount across line items by dollar weight.

    The last item absorbs any rounding remainder so the sum is exact.
    If all items are free (subtotal == 0), distributes by quantity instead.
    """
    if freight_amount == Decimal("0"):
        return line_items

    subtotal = sum(li.subtotal_cost for li in line_items)

    if subtotal == Decimal("0"):
        total_qty = sum(li.quantity for li in line_items)
        for li in line_items:
            li.total_cost = freight_amount * (Decimal(li.quantity) / Decimal(total_qty))
        return line_items

    allocated_so_far = Decimal("0")
    for i, li in enumerate(line_items):
        if i == len(line_items) - 1:
            li.total_cost = li.subtotal_cost + (freight_amount - allocated_so_far)
        else:
            share = (li.subtotal_cost / subtotal * freight_amount).quantize(
                Decimal("0.01")
            )
            li.total_cost = li.subtotal_cost + share
            allocated_so_far += share

    return line_items
