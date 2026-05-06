from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Optional


@dataclass
class Account:
    id: int
    code: str
    name: str
    type: str                        # asset | liability | equity | income | expense
    subtype: Optional[str]           # bank | credit_card | inventory | cogs | cash | tax | clearing
    is_active: bool
    is_personal_funds_proxy: bool    # True only for Owner Contributions
    is_system_protected: bool        # True for accounts that cannot be deleted
    created_at: datetime


@dataclass
class JournalEntry:
    id: int
    entry_date: date
    description: str
    template_id: str
    total_amount: Decimal
    created_at: datetime
    created_by_method: str           # questionnaire | manual | import_ebay | system_auto
    vendor: Optional[str] = None
    notes: Optional[str] = None
    is_void: bool = False
    void_reason: Optional[str] = None
    void_at: Optional[datetime] = None


@dataclass
class JournalLine:
    id: int
    entry_id: int
    account_id: int
    debit: Decimal                   # exactly one of debit/credit is > 0; the other is 0
    credit: Decimal
    memo: Optional[str] = None
    inventory_item_id: Optional[int] = None  # FK → items.id when line touches inventory


@dataclass
class Receipt:
    id: int
    entry_id: int
    original_filename: str
    stored_path: str                 # relative: data/receipts/YYYY/MM/{uuid}.ext
    mime_type: str
    file_size_bytes: int
    sha256: str
    uploaded_at: datetime


@dataclass
class SalesTaxRate:
    id: int
    jurisdiction: str               # e.g. "North Carolina"
    rate: Decimal                   # e.g. Decimal("0.0475")
    is_default: bool
    effective_from: date
    effective_to: Optional[date] = None
