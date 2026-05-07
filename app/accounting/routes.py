from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import asdict
from datetime import date as date_type, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from app.deps import BASE_DIR, get_db, render
from app.accounting.catalog import CATALOG, get_template, resolve_lines
from app.accounting.exceptions import EmptyEntryError, UnbalancedEntryError, VoidedEntryError
from app.accounting.posting import PostEntryRequest, post_entry, void_entry
from app.accounting.questionnaire import (
    QuestionnaireSession,
    build_answer_set,
    go_back,
    is_complete,
    next_visible_step,
    record_answer,
    step_progress,
)
from app.accounting.schemas import TransactionAnswerSet

router = APIRouter()

_ACCOUNT_TYPES = ("asset", "liability", "equity", "income", "expense")
_TYPE_LABELS = {
    "asset":     "Assets",
    "liability": "Liabilities",
    "equity":    "Equity",
    "income":    "Income",
    "expense":   "Expenses",
}


@router.get("/", include_in_schema=False)
def accounting_index():
    return RedirectResponse(url="/accounting/accounts")


# ── Chart of Accounts ─────────────────────────────────────────────────────────

@router.get("/accounts", response_class=HTMLResponse)
def accounts_list(
    request: Request,
    saved: int = 0,
    error: str = "",
    conn: sqlite3.Connection = Depends(get_db),
):
    rows = conn.execute(
        "SELECT * FROM accounts ORDER BY type, code"
    ).fetchall()
    groups = {t: [] for t in _ACCOUNT_TYPES}
    for row in rows:
        groups[row["type"]].append(row)
    return render(
        "accounting/accounts.html", request,
        groups=groups,
        type_labels=_TYPE_LABELS,
        account_types=_ACCOUNT_TYPES,
        saved=saved == 1,
        error=error,
    )


@router.post("/accounts")
def accounts_create(
    code: str = Form(...),
    name: str = Form(...),
    account_type: str = Form(...),
    subtype: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    code = code.strip().upper()
    name = name.strip()
    if not code or not name or account_type not in _ACCOUNT_TYPES:
        return RedirectResponse(
            url="/accounting/accounts?error=Missing+or+invalid+fields",
            status_code=303,
        )
    try:
        with conn:
            conn.execute(
                "INSERT INTO accounts (code, name, type, subtype) VALUES (?,?,?,?)",
                (code, name, account_type, subtype.strip() or None),
            )
    except sqlite3.IntegrityError:
        return RedirectResponse(
            url=f"/accounting/accounts?error=Account+code+{code}+already+exists",
            status_code=303,
        )
    return RedirectResponse(url="/accounting/accounts?saved=1", status_code=303)


@router.post("/accounts/{account_id}")
def accounts_update(
    account_id: int,
    action: str = Form(...),
    name: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    row = conn.execute(
        "SELECT * FROM accounts WHERE id=?", (account_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Account not found")

    if action == "rename":
        name = name.strip()
        if not name:
            return RedirectResponse(
                url="/accounting/accounts?error=Name+cannot+be+blank",
                status_code=303,
            )
        with conn:
            conn.execute(
                "UPDATE accounts SET name=? WHERE id=?", (name, account_id)
            )

    elif action == "toggle":
        if row["is_system_protected"]:
            return RedirectResponse(
                url="/accounting/accounts?error=Cannot+deactivate+a+system-protected+account",
                status_code=303,
            )
        with conn:
            conn.execute(
                "UPDATE accounts SET is_active=? WHERE id=?",
                (0 if row["is_active"] else 1, account_id),
            )

    return RedirectResponse(url="/accounting/accounts?saved=1", status_code=303)


# ── Manual Entry Form (developer harness) ─────────────────────────────────────

@router.get("/entry/manual", response_class=HTMLResponse)
def entry_manual_form(
    request: Request,
    error: str = "",
    conn: sqlite3.Connection = Depends(get_db),
):
    accounts = conn.execute(
        "SELECT id, code, name, type FROM accounts WHERE is_active=1 ORDER BY type, code"
    ).fetchall()
    templates = sorted(CATALOG.values(), key=lambda t: t.id)
    return render(
        "accounting/entry_manual.html", request,
        templates=templates,
        accounts=accounts,
        error=error,
        today=date_type.today().isoformat(),
    )


@router.post("/entry/manual")
def entry_manual_submit(
    template_id: str = Form(...),
    entry_date: str = Form(...),
    total_amount: str = Form(...),
    description: str = Form(""),
    vendor: str = Form(""),
    memo: str = Form(""),
    notes: str = Form(""),
    payment_account_id: str = Form(""),
    expense_category_account_id: str = Form(""),
    sales_tax_amount: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    def err(msg: str) -> RedirectResponse:
        return RedirectResponse(
            url=f"/accounting/entry/manual?error={quote(msg)}",
            status_code=303,
        )

    try:
        tmpl = get_template(template_id)
    except KeyError:
        return err(f"Unknown template: {template_id}")

    try:
        amount = Decimal(total_amount.strip())
    except InvalidOperation:
        return err("Invalid amount — enter a number like 12.50")

    try:
        parsed_date = date_type.fromisoformat(entry_date.strip())
    except ValueError:
        return err("Invalid date")

    pay_id = int(payment_account_id) if payment_account_id.strip() else None
    exp_id = int(expense_category_account_id) if expense_category_account_id.strip() else None
    tax = Decimal(sales_tax_amount.strip()) if sales_tax_amount.strip() else None

    account_map = {
        r["code"]: r["id"]
        for r in conn.execute("SELECT code, id FROM accounts").fetchall()
    }

    try:
        lines = resolve_lines(
            template_id=template_id,
            total_amount=amount,
            account_map=account_map,
            payment_account_id=pay_id,
            expense_category_account_id=exp_id,
            sales_tax_amount=tax,
            memo=memo.strip() or None,
        )
    except (ValueError, KeyError) as exc:
        return err(str(exc))

    req = PostEntryRequest(
        entry_date=parsed_date,
        description=description.strip() or f"Manual: {tmpl.name}",
        template_id=template_id,
        total_amount=amount,
        lines=lines,
        created_by_method="manual",
        vendor=vendor.strip() or None,
        notes=notes.strip() or None,
    )

    try:
        post_entry(conn, req)
    except (UnbalancedEntryError, EmptyEntryError) as exc:
        return err(str(exc))

    return RedirectResponse(url="/accounting/entries?saved=1", status_code=303)


# ── Entry List ─────────────────────────────────────────────────────────────────

@router.get("/entries", response_class=HTMLResponse)
def entries_list(
    request: Request,
    saved: int = 0,
    from_date: str = "",
    to_date: str = "",
    filter_template: str = "",
    conn: sqlite3.Connection = Depends(get_db),
):
    conditions: list[str] = []
    params: list[str] = []
    if from_date:
        conditions.append("je.entry_date >= ?")
        params.append(from_date)
    if to_date:
        conditions.append("je.entry_date <= ?")
        params.append(to_date)
    if filter_template:
        conditions.append("je.template_id = ?")
        params.append(filter_template)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    entries = conn.execute(
        f"""
        SELECT je.id, je.entry_date, je.template_id, je.description,
               je.vendor, je.total_amount, je.is_void, je.created_by_method
        FROM journal_entries je
        {where}
        ORDER BY je.entry_date DESC, je.id DESC
        """,
        params,
    ).fetchall()

    return render(
        "accounting/entry_list.html", request,
        entries=entries,
        template_ids=sorted(CATALOG.keys()),
        from_date=from_date,
        to_date=to_date,
        filter_template=filter_template,
        saved=saved == 1,
    )


# ── Entry Detail + Void ────────────────────────────────────────────────────────

@router.get("/entries/{entry_id}", response_class=HTMLResponse)
def entry_detail(
    request: Request,
    entry_id: int,
    voided: int = 0,
    error: str = "",
    warn: str = "",
    conn: sqlite3.Connection = Depends(get_db),
):
    entry = conn.execute(
        "SELECT * FROM journal_entries WHERE id=?", (entry_id,)
    ).fetchone()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    lines = conn.execute(
        """
        SELECT jl.id, jl.debit, jl.credit, jl.memo,
               a.code AS account_code, a.name AS account_name
        FROM journal_lines jl
        JOIN accounts a ON a.id = jl.account_id
        WHERE jl.entry_id = ?
        ORDER BY jl.id
        """,
        (entry_id,),
    ).fetchall()

    receipts = conn.execute(
        "SELECT * FROM receipts WHERE entry_id=? ORDER BY uploaded_at",
        (entry_id,),
    ).fetchall()

    return render(
        "accounting/entry_detail.html", request,
        entry=entry,
        lines=lines,
        receipts=receipts,
        voided=voided == 1,
        error=error,
        warn=warn,
    )


@router.post("/entries/{entry_id}/void")
def entry_void(
    entry_id: int,
    void_reason: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    if not void_reason.strip():
        return RedirectResponse(
            url=f"/accounting/entries/{entry_id}?error=Void+reason+is+required",
            status_code=303,
        )
    try:
        void_entry(conn, entry_id, void_reason.strip())
    except VoidedEntryError as exc:
        return RedirectResponse(
            url=f"/accounting/entries/{entry_id}?error={quote(str(exc))}",
            status_code=303,
        )
    except ValueError as exc:
        return RedirectResponse(
            url=f"/accounting/entries/{entry_id}?error={quote(str(exc))}",
            status_code=303,
        )
    return RedirectResponse(
        url=f"/accounting/entries/{entry_id}?voided=1",
        status_code=303,
    )


# ── Receipt Upload + Streaming ─────────────────────────────────────────────────

_RECEIPTS_DIR = BASE_DIR / "data" / "receipts"


@router.post("/entries/{entry_id}/receipts")
async def entry_attach_receipt(
    entry_id: int,
    file: UploadFile = File(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    entry = conn.execute(
        "SELECT id FROM journal_entries WHERE id=?", (entry_id,)
    ).fetchone()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    content = await file.read()
    if not content:
        return RedirectResponse(
            url=f"/accounting/entries/{entry_id}?error=Uploaded+file+is+empty",
            status_code=303,
        )

    sha256 = hashlib.sha256(content).hexdigest()
    original_filename = file.filename or "receipt"
    ext = Path(original_filename).suffix.lower()
    file_uuid = uuid.uuid4().hex
    now = datetime.utcnow()

    rel_path = (
        Path("data") / "receipts"
        / str(now.year)
        / f"{now.month:02d}"
        / f"{file_uuid}{ext}"
    )
    abs_path = BASE_DIR / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(content)

    duplicate = conn.execute(
        "SELECT id FROM receipts WHERE sha256=?", (sha256,)
    ).fetchone()

    with conn:
        conn.execute(
            """
            INSERT INTO receipts
              (entry_id, original_filename, stored_path, mime_type, file_size_bytes, sha256)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id,
                original_filename,
                str(rel_path),
                file.content_type or "application/octet-stream",
                len(content),
                sha256,
            ),
        )

    if duplicate:
        return RedirectResponse(
            url=f"/accounting/entries/{entry_id}?warn=duplicate",
            status_code=303,
        )
    return RedirectResponse(
        url=f"/accounting/entries/{entry_id}",
        status_code=303,
    )


@router.get("/receipts/{receipt_id}/file")
def receipt_file(
    receipt_id: int,
    conn: sqlite3.Connection = Depends(get_db),
):
    receipt = conn.execute(
        "SELECT * FROM receipts WHERE id=?", (receipt_id,)
    ).fetchone()
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")

    abs_path = BASE_DIR / receipt["stored_path"]
    if not abs_path.exists():
        raise HTTPException(status_code=404, detail="Receipt file not found on disk")

    return FileResponse(
        path=str(abs_path),
        media_type=receipt["mime_type"],
        filename=receipt["original_filename"],
    )


# ── Questionnaire session helpers ─────────────────────────────────────────────

def _load_session(conn: sqlite3.Connection, session_id: str) -> QuestionnaireSession:
    row = conn.execute(
        "SELECT data FROM questionnaire_sessions WHERE session_id=?", (session_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    d = json.loads(row["data"])
    return QuestionnaireSession(**d)


def _save_session(conn: sqlite3.Connection, session: QuestionnaireSession) -> None:
    session.updated_at = datetime.utcnow().isoformat()
    payload = json.dumps(asdict(session), default=str)
    with conn:
        conn.execute(
            """
            INSERT INTO questionnaire_sessions (session_id, data, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at
            """,
            (session.session_id, payload, session.created_at, session.updated_at),
        )


def _delete_session(conn: sqlite3.Connection, session_id: str) -> None:
    with conn:
        conn.execute(
            "DELETE FROM questionnaire_sessions WHERE session_id=?", (session_id,)
        )


# ── Questionnaire routes ──────────────────────────────────────────────────────

@router.get("/entry/start")
def entry_start(conn: sqlite3.Connection = Depends(get_db)):
    """Create a fresh session and redirect to the first step."""
    session = QuestionnaireSession()
    _save_session(conn, session)
    return RedirectResponse(
        url=f"/accounting/entry/step/{session.session_id}",
        status_code=303,
    )


@router.get("/entry/step/{session_id}", response_class=HTMLResponse)
def entry_step(
    session_id: str,
    request: Request,
    error: str = "",
    conn: sqlite3.Connection = Depends(get_db),
):
    session = _load_session(conn, session_id)
    step = next_visible_step(session)

    if step is None:
        # All required steps answered — redirect to preview
        return RedirectResponse(
            url=f"/accounting/entry/preview/{session_id}",
            status_code=303,
        )

    done, total = step_progress(session)

    # For account_picker steps: fetch matching accounts for the dropdown
    accounts = []
    if step.input_type == "account_picker" and step.account_filter:
        subtypes = [s.strip() for s in step.account_filter.split(",")]
        placeholders = ",".join("?" * len(subtypes))
        accounts = conn.execute(
            f"SELECT id, code, name, type, subtype FROM accounts "
            f"WHERE (subtype IN ({placeholders}) OR type IN ({placeholders})) "
            f"AND is_active=1 ORDER BY type, code",
            subtypes + subtypes,
        ).fetchall()

    # For inventory_picker: fetch existing inventory items
    inventory_items = []
    if step.input_type == "inventory_picker":
        inventory_items = conn.execute(
            "SELECT id, sku, name FROM items ORDER BY name"
        ).fetchall()

    return render(
        "accounting/questionnaire_step.html", request,
        session_id=session_id,
        step=step,
        answers=session.answers,
        done=done,
        total=total,
        accounts=accounts,
        inventory_items=inventory_items,
        error=error,
    )


@router.post("/entry/answer")
def entry_answer(
    session_id: str = Form(...),
    step_id: str = Form(...),
    answer: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    session = _load_session(conn, session_id)

    from app.accounting.questionnaire import _STEP_BY_ID
    step = _STEP_BY_ID.get(step_id)
    if not step:
        raise HTTPException(status_code=400, detail=f"Unknown step: {step_id}")

    # Parse JSON answers for structured types
    raw: object
    if step.input_type == "inventory_picker" and answer:
        try:
            raw = json.loads(answer)
        except json.JSONDecodeError:
            raw = answer
    else:
        raw = answer if answer != "" else None

    try:
        record_answer(session, step_id, raw)
    except ValueError as exc:
        return RedirectResponse(
            url=f"/accounting/entry/step/{session_id}?error={quote(str(exc))}",
            status_code=303,
        )

    _save_session(conn, session)
    return RedirectResponse(
        url=f"/accounting/entry/step/{session_id}",
        status_code=303,
    )


@router.post("/entry/back")
def entry_back(
    session_id: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    session = _load_session(conn, session_id)
    go_back(session)
    _save_session(conn, session)
    return RedirectResponse(
        url=f"/accounting/entry/step/{session_id}",
        status_code=303,
    )


def _build_summary_text(
    tas: TransactionAnswerSet,
    template_name: str,
    payment_account_name: Optional[str],
    expense_account_name: Optional[str],
    inventory_item_name: Optional[str],
) -> str:
    """Return a plain-English sentence describing the transaction."""
    amt = f"${tas.total_amount:.2f}"
    tid = tas.template_id

    # Inventory item description ("3 Funko Pops" or just "items")
    item_desc: Optional[str] = None
    if tas.inventory_link:
        qty = tas.inventory_link.quantity or 1
        name = inventory_item_name or tas.inventory_link.name or "items"
        item_desc = f'{qty} “{name}”' if qty > 1 else f'“{name}”'

    vendor_part = f" from {tas.vendor}" if tas.vendor else ""

    if tid in ("BUY_INVENTORY", "BUY_INVENTORY_PERSONAL"):
        freight = tas.freight_in_amount or Decimal("0")
        if freight > 0:
            item_cost = tas.total_amount - freight
            cost_part = f"${item_cost:.2f} + ${freight:.2f} shipping = {amt} total"
        else:
            cost_part = amt
        items_part = f" {item_desc}" if item_desc else ""
        if tid == "BUY_INVENTORY_PERSONAL":
            return (
                f"You bought{items_part} for {cost_part}{vendor_part} using personal funds. "
                f"The business owes you {amt} back."
            )
        paid = f"your {payment_account_name}" if payment_account_name else "a business account"
        return f"You bought{items_part} for {cost_part}{vendor_part} using {paid}."

    if tid == "SELL_INVENTORY_CASH":
        tax = tas.sales_tax_amount or Decimal("0")
        if tax > 0:
            revenue = tas.total_amount - tax
            return (
                f"You recorded a sale of ${revenue:.2f} + ${tax:.2f} in sales tax "
                f"= {amt} total."
            )
        return f"You recorded a sale for {amt}."

    if tid == "REIMBURSE_OWNER":
        to_acct = f" to {payment_account_name}" if payment_account_name else ""
        return f"The business reimbursed you {amt}{to_acct}."

    if tid == "OWNER_CONTRIBUTION":
        return f"You put {amt} into the business."

    if tid == "OWNER_DRAW":
        return f"You withdrew {amt} from the business."

    if tid == "PAY_CREDIT_CARD":
        from_acct = f" from {payment_account_name}" if payment_account_name else ""
        return f"You made a {amt} credit card payment{from_acct}."

    if tid == "SALES_TAX_REMITTED":
        return f"You paid {amt} in sales tax to the state."

    if tid == "OTHER_INCOME":
        return f"You recorded {amt} in other income{vendor_part}."

    # All remaining templates are business expenses
    # (BUY_EXPENSE_PERSONAL, BUSINESS_MEAL, TRAVEL_HOTEL, etc.)
    cat = expense_account_name or template_name
    if tas.funding_source == "personal":
        return (
            f"You recorded a {amt} {cat} expense paid from personal funds. "
            f"The business owes you {amt} back."
        )
    paid = f" from {payment_account_name}" if payment_account_name else ""
    return f"You recorded a {amt} {cat} expense{paid}{vendor_part}."


@router.get("/entry/preview/{session_id}", response_class=HTMLResponse)
def entry_preview_get(
    session_id: str,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
):
    session = _load_session(conn, session_id)

    if not is_complete(session):
        return RedirectResponse(
            url=f"/accounting/entry/step/{session_id}",
            status_code=303,
        )

    answer_dict = build_answer_set(session)
    try:
        tas = TransactionAnswerSet(**answer_dict)
    except Exception as exc:
        return RedirectResponse(
            url=f"/accounting/entry/step/{session_id}?error={quote(str(exc))}",
            status_code=303,
        )

    template = get_template(tas.template_id)

    # Build account_map (code → id) and id → (code, name) for reverse lookup
    acct_rows = conn.execute("SELECT id, code, name FROM accounts").fetchall()
    account_map: dict[str, int] = {r["code"]: r["id"] for r in acct_rows}
    id_to_acct: dict[int, dict] = {r["id"]: r for r in acct_rows}

    try:
        lines = resolve_lines(
            template_id=tas.template_id,
            total_amount=tas.total_amount,
            account_map=account_map,
            payment_account_id=tas.payment_account_id,
            expense_category_account_id=tas.expense_category_account_id,
            sales_tax_amount=tas.sales_tax_amount,
            memo=tas.memo,
        )
    except Exception as exc:
        return RedirectResponse(
            url=f"/accounting/entry/step/{session_id}?error={quote(str(exc))}",
            status_code=303,
        )

    enriched_lines = []
    for ln in lines:
        acct = id_to_acct.get(ln.account_id, {})
        enriched_lines.append({
            "account_code": acct.get("code", str(ln.account_id)),
            "account_name": acct.get("name", "Unknown"),
            "debit":  str(ln.debit),
            "credit": str(ln.credit),
            "memo":   ln.memo or "",
        })

    # Resolve display names for the plain-English summary
    payment_account_name: Optional[str] = None
    if tas.payment_account_id and tas.payment_account_id in id_to_acct:
        payment_account_name = id_to_acct[tas.payment_account_id].get("name")

    expense_account_name: Optional[str] = None
    if tas.expense_category_account_id and tas.expense_category_account_id in id_to_acct:
        expense_account_name = id_to_acct[tas.expense_category_account_id].get("name")

    inventory_item_name: Optional[str] = None
    if tas.inventory_link and tas.inventory_link.mode == "link_existing" and tas.inventory_link.item_id:
        row = conn.execute(
            "SELECT name FROM items WHERE id=?", (tas.inventory_link.item_id,)
        ).fetchone()
        if row:
            inventory_item_name = row["name"]
    elif tas.inventory_link and tas.inventory_link.name:
        inventory_item_name = tas.inventory_link.name

    summary_text = _build_summary_text(
        tas, template.name, payment_account_name, expense_account_name, inventory_item_name
    )

    return render(
        "accounting/entry_confirm.html", request,
        session_id=session_id,
        tas=tas,
        template=template,
        lines=enriched_lines,
        summary_text=summary_text,
        payment_account_name=payment_account_name,
        expense_account_name=expense_account_name,
        inventory_item_name=inventory_item_name,
    )


@router.post("/entry/preview")
def entry_preview_post(
    session_id: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Redirect to the GET preview so the form confirm button works."""
    return RedirectResponse(
        url=f"/accounting/entry/preview/{session_id}",
        status_code=303,
    )


@router.post("/entry/back-to-edit")
def entry_back_to_edit(
    session_id: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Undo the last step so the user can edit from the preview screen."""
    session = _load_session(conn, session_id)
    go_back(session)
    _save_session(conn, session)
    return RedirectResponse(
        url=f"/accounting/entry/step/{session_id}",
        status_code=303,
    )


def _resolve_inventory_item_id(
    conn: sqlite3.Connection,
    tas: TransactionAnswerSet,
) -> Optional[int]:
    """Return an items.id to attach to journal lines, creating a new item if needed."""
    if tas.inventory_link is None:
        return None

    link = tas.inventory_link

    if link.mode == "link_existing":
        if link.item_id is not None:
            return link.item_id
        # Fallback: lookup by SKU
        row = conn.execute(
            "SELECT id FROM items WHERE sku=?", (link.sku,)
        ).fetchone()
        return row["id"] if row else None

    # mode == "create_new": insert a minimal resale item
    unit_cost = float(tas.total_amount) / max(link.quantity, 1)
    with conn:
        cur = conn.execute(
            """
            INSERT INTO items (item_type, name, qty_on_hand, unit_cost)
            VALUES ('resale', ?, ?, ?)
            """,
            (link.name, link.quantity, unit_cost),
        )
    return cur.lastrowid


@router.post("/entry/confirm")
async def entry_confirm(
    request: Request,
    session_id: str = Form(...),
    receipt: Optional[UploadFile] = File(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    session = _load_session(conn, session_id)

    if not is_complete(session):
        return RedirectResponse(
            url=f"/accounting/entry/step/{session_id}",
            status_code=303,
        )

    answer_dict = build_answer_set(session)
    try:
        tas = TransactionAnswerSet(**answer_dict)
    except Exception as exc:
        return RedirectResponse(
            url=f"/accounting/entry/preview/{session_id}?error={quote(str(exc))}",
            status_code=303,
        )

    template = get_template(tas.template_id)

    # Resolve inventory item (create if needed) before building lines
    try:
        inventory_item_id = _resolve_inventory_item_id(conn, tas)
    except Exception as exc:
        return RedirectResponse(
            url=f"/accounting/entry/preview/{session_id}?error={quote(str(exc))}",
            status_code=303,
        )

    # Build account_map and journal lines
    acct_rows = conn.execute("SELECT id, code FROM accounts").fetchall()
    account_map: dict[str, int] = {r["code"]: r["id"] for r in acct_rows}

    try:
        lines = resolve_lines(
            template_id=tas.template_id,
            total_amount=tas.total_amount,
            account_map=account_map,
            payment_account_id=tas.payment_account_id,
            expense_category_account_id=tas.expense_category_account_id,
            sales_tax_amount=tas.sales_tax_amount,
            memo=tas.memo,
            inventory_item_id=inventory_item_id,
        )
    except Exception as exc:
        return RedirectResponse(
            url=f"/accounting/entry/preview/{session_id}?error={quote(str(exc))}",
            status_code=303,
        )

    # Auto-generate description
    description = tas.description or template.name
    if tas.vendor:
        description = f"{description} — {tas.vendor}"

    req = PostEntryRequest(
        entry_date=tas.entry_date,
        description=description,
        template_id=tas.template_id,
        total_amount=tas.total_amount,
        lines=lines,
        vendor=tas.vendor,
        notes=tas.notes or tas.memo,
    )

    try:
        entry_id = post_entry(conn, req)
    except (EmptyEntryError, UnbalancedEntryError) as exc:
        return RedirectResponse(
            url=f"/accounting/entry/preview/{session_id}?error={quote(str(exc))}",
            status_code=303,
        )

    # Attach receipt if one was uploaded
    if receipt and receipt.filename:
        content = await receipt.read()
        if content:
            sha256 = hashlib.sha256(content).hexdigest()
            ext = Path(receipt.filename).suffix.lower()
            file_uuid = uuid.uuid4().hex
            now = datetime.utcnow()
            rel_path = (
                Path("data") / "receipts"
                / str(now.year)
                / f"{now.month:02d}"
                / f"{file_uuid}{ext}"
            )
            abs_path = BASE_DIR / rel_path
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_bytes(content)
            with conn:
                conn.execute(
                    """
                    INSERT INTO receipts
                      (entry_id, original_filename, stored_path, mime_type, file_size_bytes, sha256)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry_id,
                        receipt.filename,
                        str(rel_path),
                        receipt.content_type or "application/octet-stream",
                        len(content),
                        sha256,
                    ),
                )

    # Clean up the session — entry is posted
    _delete_session(conn, session_id)

    return RedirectResponse(
        url=f"/accounting/entries/{entry_id}",
        status_code=303,
    )
