"""
Kanban MCP tools — lets an AI (ChatGPT job search, Claude) write job leads
and task cards to SaltStocks boards.

Unlike the accounting write tools, these write directly (no mcp_pending_writes
staging): new cards land in the board's inbox column, which is the review step.
There is deliberately no delete tool — archiving is web-UI only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from app.db import get_conn
from app.services import kanban as kb

MAX_CARDS_PER_CALL = 50

# Fields returned per card — keeps responses compact for the model.
_CARD_OUT = (
    "id", "title", "column_name", "company", "location", "remote_type", "url",
    "salary_text", "source", "external_id", "posted_date", "fit_score", "notes",
    "description", "created_by", "created_at", "archived_at",
)


class JobCard(BaseModel):
    title: str = Field(description="Job title, e.g. 'Senior Data Analyst'")
    company: Optional[str] = None
    location: Optional[str] = Field(None, description="City/state or region")
    remote_type: Optional[Literal["remote", "hybrid", "onsite"]] = None
    url: Optional[str] = Field(None, description="Direct link to the listing — the main dedup key")
    salary_text: Optional[str] = Field(None, description="Salary as listed, e.g. '$85k–$100k'")
    source: Optional[str] = Field(None, description="Where it was found, e.g. 'indeed', 'linkedin'")
    external_id: Optional[str] = Field(None, description="The source site's job id, if known")
    posted_date: Optional[str] = Field(None, description="ISO date the job was posted")
    fit_score: Optional[int] = Field(None, ge=1, le=5, description="1 = poor fit … 5 = excellent fit")
    description: Optional[str] = Field(None, description="Short summary of the role")
    notes: Optional[str] = Field(None, description="Why it fits, concerns, next steps")


def _as_of() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _card_out(card: dict) -> dict:
    return {k: card.get(k) for k in _CARD_OUT}


def register(mcp: FastMCP) -> None:
    """Attach the kanban tools to the FastMCP instance."""

    @mcp.tool(
        description=(
            "Lists SaltStocks kanban boards with their columns and card counts. "
            "The job search board's slug is 'job-search'."
        )
    )
    def list_boards() -> dict:
        conn = get_conn()
        try:
            return {"boards": kb.list_boards(conn), "as_of": _as_of()}
        finally:
            conn.close()

    @mcp.tool(
        description=(
            "Returns cards on a kanban board (title, company, url, column, fit score, notes). "
            "Call this BEFORE adding job leads to see which jobs are already tracked — "
            "archived cards are included when include_archived=true, and those were rejected "
            "on purpose, so don't re-suggest them."
        )
    )
    def get_board_cards(
        board: str = "job-search",
        column: Optional[str] = None,
        include_archived: bool = False,
        limit: int = 200,
    ) -> dict:
        conn = get_conn()
        try:
            cards = kb.list_cards(conn, board, column, include_archived, limit)
            return {"board": board, "count": len(cards),
                    "cards": [_card_out(c) for c in cards], "as_of": _as_of()}
        finally:
            conn.close()

    @mcp.tool(
        description=(
            "Adds job leads to a kanban board's inbox column ('New' on the job-search board). "
            f"Send all results from one search in a single call (max {MAX_CARDS_PER_CALL}). "
            "Duplicates — same listing URL, same source+external_id, or same company+title+location "
            "as any existing or archived card — are skipped automatically and reported back. "
            "Writes immediately; Holly triages cards from the inbox in the SaltStocks web UI."
        )
    )
    def add_job_cards(board: str, jobs: list[JobCard]) -> dict:
        if not jobs:
            raise ValueError("jobs is empty — nothing to add.")
        if len(jobs) > MAX_CARDS_PER_CALL:
            raise ValueError(
                f"{len(jobs)} jobs sent; the limit is {MAX_CARDS_PER_CALL} per call. Split into batches."
            )
        conn = get_conn()
        try:
            result = kb.add_cards_batch(
                conn, board, [j.model_dump(exclude_none=True) for j in jobs], created_by="mcp"
            )
            return {
                "added_count": len(result["added"]),
                "skipped_count": len(result["skipped"]),
                **result,
                "as_of": _as_of(),
            }
        finally:
            conn.close()

    @mcp.tool(
        description=(
            "Updates fields on an existing card (e.g. add notes, adjust fit_score, fill in salary). "
            "Only the fields you pass are changed."
        )
    )
    def update_card(
        card_id: int,
        title: Optional[str] = None,
        company: Optional[str] = None,
        location: Optional[str] = None,
        remote_type: Optional[Literal["remote", "hybrid", "onsite"]] = None,
        url: Optional[str] = None,
        salary_text: Optional[str] = None,
        posted_date: Optional[str] = None,
        fit_score: Optional[int] = None,
        description: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> dict:
        changes = {k: v for k, v in {
            "title": title, "company": company, "location": location,
            "remote_type": remote_type, "url": url, "salary_text": salary_text,
            "posted_date": posted_date, "fit_score": fit_score,
            "description": description, "notes": notes,
        }.items() if v is not None}
        if not changes:
            raise ValueError("No fields to update were provided.")
        conn = get_conn()
        try:
            kb.update_card(conn, card_id, changes)
            return {"card": _card_out(kb.get_card(conn, card_id)), "as_of": _as_of()}
        finally:
            conn.close()

    @mcp.tool(
        description=(
            "Moves a card to another column on the same board, by column name "
            "(case-insensitive, e.g. 'Applied'). Only do this when Holly asks."
        )
    )
    def move_card(card_id: int, column_name: str) -> dict:
        conn = get_conn()
        try:
            card = kb.get_card(conn, card_id)
            col = kb.find_column(conn, card["board_id"], column_name)
            if col is None:
                names = [c["name"] for c in kb.get_columns(conn, card["board_id"])]
                raise ValueError(f"No column '{column_name}'. Columns: {names}")
            kb.move_card(conn, card_id, col["id"])
            return {"card": _card_out(kb.get_card(conn, card_id)), "as_of": _as_of()}
        finally:
            conn.close()
