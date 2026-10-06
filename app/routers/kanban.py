from __future__ import annotations

import sqlite3
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..db import get_conn
from ..deps import render, get_db
from ..services import kanban as kb
from ..services import kanban_import as ki

router = APIRouter(prefix="/kanban", tags=["kanban"])

_FORM_FIELDS = list(kb.CARD_FIELDS)


# ── helpers ──────────────────────────────────────────────────────────────────

async def _db_async():
    """get_db for async routes. The sync get_db opens its connection in a worker
    thread, but async endpoints run on the event loop thread — sqlite3 refuses
    cross-thread use, so async routes need a connection opened on the loop."""
    conn = get_conn()
    try:
        yield conn
    finally:
        conn.close()


def _back(url: str, *, msg: Optional[str] = None, error: Optional[str] = None) -> RedirectResponse:
    if msg or error:
        sep = "&" if "?" in url else "?"
        url += f"{sep}{'error' if error else 'msg'}={quote(error or msg)}"
    return RedirectResponse(url, status_code=303)


async def _card_form(request: Request) -> dict:
    form = await request.form()
    return {f: form.get(f, "") for f in _FORM_FIELDS if f in form}


# ── boards ───────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
def board_list(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return render("kanban/index.html", request, title="Boards", boards=kb.list_boards(conn),
                  msg=request.query_params.get("msg"), error=request.query_params.get("error"))


@router.post("")
def board_create(name: str = Form(...), columns: str = Form(""),
                 conn: sqlite3.Connection = Depends(get_db)):
    try:
        cols = [c for c in columns.split(",") if c.strip()] or None
        slug = kb.create_board(conn, name, cols)
    except kb.KanbanError as e:
        return _back("/kanban", error=str(e))
    return _back(f"/kanban/{slug}")


@router.get("/{slug}", response_class=HTMLResponse)
def board_view(slug: str, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    try:
        board = kb.get_board(conn, slug)
    except kb.KanbanError:
        return _back("/kanban", error=f"No board '{slug}'.")
    archived = kb.list_cards(conn, slug, include_archived=True, limit=1000)
    archived = [c for c in archived if c["archived_at"]]
    return render("kanban/board.html", request, title=board["name"], board=board,
                  archived=archived, remote_types=kb.REMOTE_TYPES,
                  msg=request.query_params.get("msg"), error=request.query_params.get("error"))


# ── cards ────────────────────────────────────────────────────────────────────

@router.post("/{slug}/cards")
async def card_create(slug: str, request: Request, column_id: int = Form(...),
                      conn: sqlite3.Connection = Depends(_db_async)):
    url = f"/kanban/{slug}"
    try:
        board = kb.get_board_row(conn, slug)
        kb.create_card(conn, board["id"], await _card_form(request), column_id=column_id)
    except kb.DuplicateCardError as e:
        return _back(url, error=f"That job is already on the board (card #{e.existing_card_id}).")
    except kb.KanbanError as e:
        return _back(url, error=str(e))
    return _back(url)


@router.post("/cards/{card_id}")
async def card_update(card_id: int, request: Request, conn: sqlite3.Connection = Depends(_db_async)):
    try:
        card = kb.get_card(conn, card_id)
    except kb.KanbanError as e:
        return _back("/kanban", error=str(e))
    url = f"/kanban/{card['board_slug']}"
    try:
        kb.update_card(conn, card_id, await _card_form(request))
        form = await request.form()
        if form.get("column_id") and int(form["column_id"]) != card["column_id"]:
            kb.move_card(conn, card_id, int(form["column_id"]))
    except kb.DuplicateCardError as e:
        return _back(url, error=f"That URL/job already belongs to card #{e.existing_card_id}.")
    except kb.KanbanError as e:
        return _back(url, error=str(e))
    return _back(url)


@router.post("/cards/{card_id}/move")
async def card_move(card_id: int, request: Request, conn: sqlite3.Connection = Depends(_db_async)):
    try:
        body = await request.json()
        kb.move_card(conn, card_id, int(body["column_id"]), body.get("position"))
    except (kb.KanbanError, KeyError, ValueError, TypeError) as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    return {"ok": True}


@router.post("/cards/{card_id}/archive")
def card_archive(card_id: int, archived: int = Form(1), conn: sqlite3.Connection = Depends(get_db)):
    try:
        card = kb.get_card(conn, card_id)
        kb.set_archived(conn, card_id, bool(archived))
    except kb.KanbanError as e:
        return _back("/kanban", error=str(e))
    return _back(f"/kanban/{card['board_slug']}",
                 msg=f"Archived “{card['title']}”." if archived else f"Restored “{card['title']}”.")


# ── columns ──────────────────────────────────────────────────────────────────

@router.post("/{slug}/columns")
def column_add(slug: str, name: str = Form(...), conn: sqlite3.Connection = Depends(get_db)):
    try:
        kb.add_column(conn, kb.get_board_row(conn, slug)["id"], name)
    except kb.KanbanError as e:
        return _back(f"/kanban/{slug}", error=str(e))
    return _back(f"/kanban/{slug}")


@router.post("/{slug}/columns/{column_id}")
def column_edit(slug: str, column_id: int, action: str = Form(...), name: str = Form(""),
                conn: sqlite3.Connection = Depends(get_db)):
    try:
        if action == "rename":
            kb.rename_column(conn, column_id, name)
        elif action == "left":
            kb.shift_column(conn, column_id, -1)
        elif action == "right":
            kb.shift_column(conn, column_id, 1)
        elif action == "delete":
            kb.delete_column(conn, column_id)
        else:
            raise kb.KanbanError(f"Unknown column action '{action}'.")
    except kb.KanbanError as e:
        return _back(f"/kanban/{slug}", error=str(e))
    return _back(f"/kanban/{slug}")


# ── CSV / Notion import ──────────────────────────────────────────────────────

@router.get("/{slug}/import", response_class=HTMLResponse)
def import_start(slug: str, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    try:
        board = kb.get_board_row(conn, slug)
    except kb.KanbanError as e:
        return _back("/kanban", error=str(e))
    return render("kanban/import.html", request, title="Import cards", board=board, stage="upload")


@router.post("/{slug}/import", response_class=HTMLResponse)
async def import_preview(slug: str, request: Request, file: Optional[UploadFile] = File(None),
                         conn: sqlite3.Connection = Depends(_db_async)):
    """Upload → show mapping + preview. Re-posting with remap=1 re-previews
    using the adjusted mapping."""
    try:
        board = kb.get_board_row(conn, slug)
    except kb.KanbanError as e:
        return _back("/kanban", error=str(e))
    form = await request.form()
    if file is not None and file.filename:
        csv_text = (await file.read()).decode("utf-8-sig", errors="replace")
    else:
        csv_text = form.get("csv_text", "")
    try:
        headers, rows = ki.parse_csv(csv_text)
    except Exception as e:  # ValueError, csv.Error, decode problems
        return render("kanban/import.html", request, title="Import cards", board=board,
                      stage="upload", error=f"Couldn't read that CSV: {e}")

    if form.get("remap"):
        mapping = {h: form.get(f"map_{i}", "") for i, h in enumerate(headers)}
    else:
        mapping = ki.auto_map(headers)

    cards = ki.build_cards(rows, mapping)
    existing = {c["name"].strip().lower() for c in kb.get_columns(conn, board["id"])}
    inbox = kb.inbox_column(conn, board["id"])["name"]
    new_columns = sorted({c["_column"] for c in cards
                          if c.get("_column") and c["_column"].strip().lower() not in existing})
    return render("kanban/import.html", request, title="Import cards", board=board,
                  stage="preview", csv_text=csv_text, headers=headers, mapping=mapping,
                  targets=ki.TARGETS, cards=cards, preview=cards[:25], inbox=inbox,
                  new_columns=new_columns, row_count=len(rows))


@router.post("/{slug}/import/commit", response_class=HTMLResponse)
async def import_commit(slug: str, request: Request, conn: sqlite3.Connection = Depends(_db_async)):
    try:
        board = kb.get_board_row(conn, slug)
        form = await request.form()
        headers, rows = ki.parse_csv(form.get("csv_text", ""))
    except Exception as e:
        return _back(f"/kanban/{slug}/import", error=str(e))
    mapping = {h: form.get(f"map_{i}", "") for i, h in enumerate(headers)}
    cards = ki.build_cards(rows, mapping)
    result = kb.add_cards_batch(conn, slug, cards, created_by="import", create_missing_columns=True)
    return render("kanban/import.html", request, title="Import cards", board=board,
                  stage="done", result=result)
