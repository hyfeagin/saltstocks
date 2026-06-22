"""
MCP staged-write queue.

MCP write tools call queue_write() instead of posting directly to the DB.
The web UI (GET/POST /accounting/mcp-review) lets Holly approve or reject
each queued write before it hits the real journal.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.db import DB_PATH


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def queue_write(
    tool_name: str,
    params: dict[str, Any],
    preview_lines: list[dict],
    human_summary: str,
) -> int:
    """Insert a pending write into mcp_pending_writes. Returns the new row id."""
    conn = _get_conn()
    try:
        cur = conn.execute(
            """
            INSERT INTO mcp_pending_writes (tool_name, params_json, preview_json, human_summary)
            VALUES (?, ?, ?, ?)
            """,
            (tool_name, json.dumps(params), json.dumps(preview_lines), human_summary),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_pending_writes(conn: sqlite3.Connection) -> list[dict]:
    """Return all pending writes, oldest first."""
    rows = conn.execute(
        "SELECT * FROM mcp_pending_writes WHERE status='pending' ORDER BY created_at"
    ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["preview_lines"] = json.loads(d["preview_json"])
        d["params"] = json.loads(d["params_json"])
        result.append(d)
    return result


def get_pending_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM mcp_pending_writes WHERE status='pending'"
    ).fetchone()[0]


def approve_write(pending_id: int) -> dict:
    """
    Re-run the original tool call against the real DB.
    Marks status='approved' on success; raises on any failure (so the caller
    can show an inline error and leave the card visible).
    """
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT tool_name, params_json FROM mcp_pending_writes WHERE id=? AND status='pending'",
            (pending_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Pending write #{pending_id} not found or already processed.")

        tool_name = row["tool_name"]
        params = json.loads(row["params_json"])
    finally:
        conn.close()

    # Dispatch to the private _execute_* functions in tools.py
    from app.mcp import tools as _tools
    dispatch = {
        "record_expense": _tools._execute_record_expense,
        "record_inventory_purchase": _tools._execute_record_inventory_purchase,
        "record_sale": _tools._execute_record_sale,
    }
    fn = dispatch.get(tool_name)
    if fn is None:
        raise ValueError(f"Unknown tool_name '{tool_name}' — cannot approve.")

    result = fn(params)

    # Mark approved
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE mcp_pending_writes SET status='approved' WHERE id=?", (pending_id,)
        )
        conn.commit()
    finally:
        conn.close()

    return result


def reject_write(conn: sqlite3.Connection, pending_id: int) -> None:
    conn.execute(
        "UPDATE mcp_pending_writes SET status='rejected' WHERE id=?", (pending_id,)
    )
    conn.commit()


def cleanup_completed(conn: sqlite3.Connection) -> None:
    """Delete all approved/rejected rows, leaving only any still-pending ones."""
    conn.execute("DELETE FROM mcp_pending_writes WHERE status != 'pending'")
    conn.commit()
