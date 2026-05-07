from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional


ZERO = Decimal("0.00")


@dataclass(frozen=True)
class ReportLine:
    account_id: int
    account_code: str
    account_name: str
    amount: Decimal


@dataclass(frozen=True)
class LedgerLine:
    entry_id: int
    entry_date: str
    description: str
    template_id: str
    debit: Decimal
    credit: Decimal
    memo: Optional[str]
    running_balance: Decimal


def _dec(value: object) -> Decimal:
    return Decimal(str(value or "0")).quantize(Decimal("0.01"))


def _normal_balance_sign(account_type: str) -> int:
    return 1 if account_type in {"asset", "expense"} else -1


def _signed_balance(account_type: str, debit: Decimal, credit: Decimal) -> Decimal:
    sign = _normal_balance_sign(account_type)
    return ((debit - credit) * Decimal(sign)).quantize(Decimal("0.01"))


def _date_clause(
    *,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    as_of: Optional[str] = None,
) -> tuple[str, list[str]]:
    clauses = ["je.is_void = 0"]
    params: list[str] = []
    if from_date:
        clauses.append("je.entry_date >= ?")
        params.append(from_date)
    if to_date:
        clauses.append("je.entry_date <= ?")
        params.append(to_date)
    if as_of:
        clauses.append("je.entry_date <= ?")
        params.append(as_of)
    return " AND ".join(clauses), params


def profit_and_loss(
    conn: sqlite3.Connection,
    *,
    from_date: str,
    to_date: str,
) -> dict:
    where, params = _date_clause(from_date=from_date, to_date=to_date)
    rows = conn.execute(
        f"""
        SELECT
          a.id AS account_id,
          a.code AS account_code,
          a.name AS account_name,
          a.type AS account_type,
          a.subtype AS account_subtype,
          SUM(CAST(jl.debit AS REAL)) AS debit_total,
          SUM(CAST(jl.credit AS REAL)) AS credit_total
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.entry_id
        JOIN accounts a ON a.id = jl.account_id
        WHERE {where}
          AND a.type IN ('income', 'expense')
        GROUP BY a.id, a.code, a.name, a.type, a.subtype
        ORDER BY a.code
        """,
        params,
    ).fetchall()

    revenue: list[ReportLine] = []
    cogs: list[ReportLine] = []
    operating_expenses: list[ReportLine] = []

    for row in rows:
        debit = _dec(row["debit_total"])
        credit = _dec(row["credit_total"])
        amount = _signed_balance(row["account_type"], debit, credit)
        if amount == ZERO:
            continue

        line = ReportLine(
            account_id=row["account_id"],
            account_code=row["account_code"],
            account_name=row["account_name"],
            amount=amount,
        )
        if row["account_type"] == "income":
            revenue.append(line)
        elif row["account_subtype"] == "cogs" or row["account_code"] == "5000":
            cogs.append(line)
        else:
            operating_expenses.append(line)

    total_revenue = sum((line.amount for line in revenue), ZERO)
    total_cogs = sum((line.amount for line in cogs), ZERO)
    total_expenses = sum((line.amount for line in operating_expenses), ZERO)
    gross_profit = (total_revenue - total_cogs).quantize(Decimal("0.01"))
    net_income = (gross_profit - total_expenses).quantize(Decimal("0.01"))

    return {
        "from_date": from_date,
        "to_date": to_date,
        "revenue": revenue,
        "cogs": cogs,
        "operating_expenses": operating_expenses,
        "total_revenue": total_revenue,
        "total_cogs": total_cogs,
        "gross_profit": gross_profit,
        "total_expenses": total_expenses,
        "net_income": net_income,
    }


def balance_sheet(
    conn: sqlite3.Connection,
    *,
    as_of: str,
) -> dict:
    where, params = _date_clause(as_of=as_of)
    rows = conn.execute(
        f"""
        SELECT
          a.id AS account_id,
          a.code AS account_code,
          a.name AS account_name,
          a.type AS account_type,
          SUM(CAST(jl.debit AS REAL)) AS debit_total,
          SUM(CAST(jl.credit AS REAL)) AS credit_total
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.entry_id
        JOIN accounts a ON a.id = jl.account_id
        WHERE {where}
          AND a.type IN ('asset', 'liability', 'equity')
        GROUP BY a.id, a.code, a.name, a.type
        ORDER BY a.code
        """,
        params,
    ).fetchall()

    assets: list[ReportLine] = []
    liabilities: list[ReportLine] = []
    equity: list[ReportLine] = []

    for row in rows:
        debit = _dec(row["debit_total"])
        credit = _dec(row["credit_total"])
        amount = _signed_balance(row["account_type"], debit, credit)
        if amount == ZERO:
            continue

        line = ReportLine(
            account_id=row["account_id"],
            account_code=row["account_code"],
            account_name=row["account_name"],
            amount=amount,
        )
        if row["account_type"] == "asset":
            assets.append(line)
        elif row["account_type"] == "liability":
            liabilities.append(line)
        else:
            equity.append(line)

    pnl_rows = conn.execute(
        f"""
        SELECT
          a.type AS account_type,
          SUM(CAST(jl.debit AS REAL)) AS debit_total,
          SUM(CAST(jl.credit AS REAL)) AS credit_total
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.entry_id
        JOIN accounts a ON a.id = jl.account_id
        WHERE {where}
          AND a.type IN ('income', 'expense')
        GROUP BY a.type
        """,
        params,
    ).fetchall()
    income_total = ZERO
    expense_total = ZERO
    for row in pnl_rows:
        amount = _signed_balance(row["account_type"], _dec(row["debit_total"]), _dec(row["credit_total"]))
        if row["account_type"] == "income":
            income_total += amount
        else:
            expense_total += amount
    current_earnings = (income_total - expense_total).quantize(Decimal("0.01"))
    if current_earnings != ZERO:
        equity.append(
            ReportLine(
                account_id=0,
                account_code="CURRENT",
                account_name="Current Earnings",
                amount=current_earnings,
            )
        )

    total_assets = sum((line.amount for line in assets), ZERO)
    total_liabilities = sum((line.amount for line in liabilities), ZERO)
    total_equity = sum((line.amount for line in equity), ZERO)

    return {
        "as_of": as_of,
        "assets": assets,
        "liabilities": liabilities,
        "equity": equity,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "total_equity": total_equity,
        "total_liabilities_and_equity": (total_liabilities + total_equity).quantize(Decimal("0.01")),
    }


def expenses_by_category(
    conn: sqlite3.Connection,
    *,
    from_date: str,
    to_date: str,
) -> dict:
    where, params = _date_clause(from_date=from_date, to_date=to_date)
    rows = conn.execute(
        f"""
        SELECT
          a.id AS account_id,
          a.code AS account_code,
          a.name AS account_name,
          SUM(CAST(jl.debit AS REAL)) AS debit_total,
          SUM(CAST(jl.credit AS REAL)) AS credit_total
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.entry_id
        JOIN accounts a ON a.id = jl.account_id
        WHERE {where}
          AND a.type = 'expense'
        GROUP BY a.id, a.code, a.name
        HAVING ROUND(SUM(CAST(jl.debit AS REAL)) - SUM(CAST(jl.credit AS REAL)), 2) != 0
        ORDER BY a.code
        """,
        params,
    ).fetchall()

    categories = [
        ReportLine(
            account_id=row["account_id"],
            account_code=row["account_code"],
            account_name=row["account_name"],
            amount=(_dec(row["debit_total"]) - _dec(row["credit_total"])).quantize(Decimal("0.01")),
        )
        for row in rows
    ]

    return {
        "from_date": from_date,
        "to_date": to_date,
        "categories": categories,
        "total_expenses": sum((line.amount for line in categories), ZERO),
    }


def general_ledger(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> dict:
    account = conn.execute(
        "SELECT id, code, name, type FROM accounts WHERE id=?",
        (account_id,),
    ).fetchone()
    if account is None:
        raise ValueError(f"Account {account_id} not found.")

    running_balance = ZERO
    if from_date:
        opening = conn.execute(
            """
            SELECT
              COALESCE(SUM(CAST(jl.debit AS REAL)), 0) AS debit_total,
              COALESCE(SUM(CAST(jl.credit AS REAL)), 0) AS credit_total
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.entry_id
            WHERE je.is_void = 0
              AND jl.account_id = ?
              AND je.entry_date < ?
            """,
            (account_id, from_date),
        ).fetchone()
        running_balance = _signed_balance(
            account["type"],
            _dec(opening["debit_total"]),
            _dec(opening["credit_total"]),
        )

    where, params = _date_clause(from_date=from_date, to_date=to_date)
    rows = conn.execute(
        f"""
        SELECT
          je.id AS entry_id,
          je.entry_date,
          je.description,
          je.template_id,
          jl.debit,
          jl.credit,
          jl.memo
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.entry_id
        WHERE {where}
          AND jl.account_id = ?
        ORDER BY je.entry_date, je.id, jl.id
        """,
        [*params, account_id],
    ).fetchall()

    lines: list[LedgerLine] = []
    for row in rows:
        debit = _dec(row["debit"])
        credit = _dec(row["credit"])
        running_balance = (
            running_balance + _signed_balance(account["type"], debit, credit)
        ).quantize(Decimal("0.01"))
        lines.append(
            LedgerLine(
                entry_id=row["entry_id"],
                entry_date=row["entry_date"],
                description=row["description"],
                template_id=row["template_id"],
                debit=debit,
                credit=credit,
                memo=row["memo"],
                running_balance=running_balance,
            )
        )

    return {
        "account_id": account["id"],
        "account_code": account["code"],
        "account_name": account["name"],
        "account_type": account["type"],
        "from_date": from_date,
        "to_date": to_date,
        "opening_balance": running_balance - sum(
            (_signed_balance(account["type"], line.debit, line.credit) for line in lines),
            ZERO,
        ),
        "lines": lines,
        "closing_balance": running_balance,
    }
