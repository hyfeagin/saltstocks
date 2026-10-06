"""
Kanban board service layer.

Plain functions over a sqlite3 connection, shared by the web routes
(app/routers/kanban.py), the MCP tools (app/mcp/kanban_tools.py) and the
Notion CSV importer. Mutating functions commit before returning.

Dedup: every card with a URL, external id, or company gets a dedup_key.
UNIQUE(board_id, dedup_key) means a job can never land on the same board
twice — archived cards keep their key, so rejected jobs stay gone.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any, Iterable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit

CARD_FIELDS = (
    "title", "description", "company", "location", "remote_type", "url",
    "salary_text", "source", "external_id", "posted_date", "fit_score", "notes",
)
REMOTE_TYPES = ("remote", "hybrid", "onsite")

# Query params that only track the click — dropped when canonicalising URLs.
# Identifying params (e.g. Indeed's ?jk=) are kept.
_TRACKING_PARAMS = {
    "ref", "refid", "trackingid", "from", "src", "fbclid", "gclid", "tk",
    "vjs", "advn", "adid", "sid", "xkcb", "currentjobid", "eid", "lipi",
    "trk", "trkinfo", "spa", "camk", "pos", "position", "pagenum",
}


class KanbanError(ValueError):
    """Bad input or a missing board/column/card — safe to show to the user."""


class DuplicateCardError(KanbanError):
    def __init__(self, existing_card_id: int):
        super().__init__(f"Duplicate of card #{existing_card_id}")
        self.existing_card_id = existing_card_id


# ── helpers ──────────────────────────────────────────────────────────────────

def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "board"


def canonical_url(url: Optional[str]) -> Optional[str]:
    if not url or not url.strip():
        return None
    parts = urlsplit(url.strip())
    if not parts.netloc:
        return url.strip().lower()
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/")
    query = sorted(
        (k, v) for k, v in parse_qsl(parts.query)
        if k.lower() not in _TRACKING_PARAMS and not k.lower().startswith("utm_")
    )
    return f"{host}{path}" + (f"?{urlencode(query)}" if query else "")


def _norm(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def compute_dedup_key(
    *,
    url: Optional[str] = None,
    source: Optional[str] = None,
    external_id: Optional[str] = None,
    company: Optional[str] = None,
    title: Optional[str] = None,
    location: Optional[str] = None,
) -> Optional[str]:
    """URL first, then source:external_id, then company|title|location.
    Returns None for plain task cards (no URL, id or company) so they never collide."""
    cu = canonical_url(url)
    if cu:
        return f"url:{cu}"
    if external_id and external_id.strip():
        return f"id:{_norm(source)}:{external_id.strip()}"
    if company and company.strip():
        return f"job:{_norm(company)}|{_norm(title)}|{_norm(location)}"
    return None


def _clean_fields(data: dict[str, Any], *, require_title: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in CARD_FIELDS:
        if key not in data:
            continue
        val = data[key]
        if isinstance(val, str):
            val = val.strip() or None
        if key == "fit_score" and val is not None:
            try:
                val = int(val)
            except (TypeError, ValueError):
                raise KanbanError(f"fit_score must be a whole number 1–5, got {data[key]!r}")
            if not 1 <= val <= 5:
                raise KanbanError(f"fit_score must be between 1 and 5, got {val}")
        if key == "remote_type" and val is not None:
            val = val.lower().replace("-", "").replace(" ", "")
            val = {"onsite": "onsite", "inoffice": "onsite", "office": "onsite"}.get(val, val)
            if val not in REMOTE_TYPES:
                raise KanbanError(f"remote_type must be one of {REMOTE_TYPES}, got {data[key]!r}")
        out[key] = val
    if require_title and not out.get("title"):
        raise KanbanError("Card title is required.")
    if "title" in data and not out.get("title"):
        raise KanbanError("Card title cannot be blank.")
    return out


def _dedup_for(fields: dict[str, Any]) -> Optional[str]:
    return compute_dedup_key(**{k: fields.get(k) for k in
                                ("url", "source", "external_id", "company", "title", "location")})


def _find_by_dedup(conn: sqlite3.Connection, board_id: int, key: Optional[str],
                   exclude_card_id: Optional[int] = None) -> Optional[int]:
    if key is None:
        return None
    row = conn.execute(
        "SELECT id FROM kanban_cards WHERE board_id=? AND dedup_key=? AND id IS NOT ?",
        (board_id, key, exclude_card_id),
    ).fetchone()
    return row["id"] if row else None


def _renumber(conn: sqlite3.Connection, column_id: int, ordered_ids: Iterable[int]) -> None:
    for pos, cid in enumerate(ordered_ids):
        conn.execute("UPDATE kanban_cards SET position=? WHERE id=?", (pos, cid))


def _column_card_ids(conn: sqlite3.Connection, column_id: int, exclude: Optional[int] = None) -> list[int]:
    rows = conn.execute(
        "SELECT id FROM kanban_cards WHERE column_id=? AND archived_at IS NULL AND id IS NOT ? "
        "ORDER BY position, id",
        (column_id, exclude),
    ).fetchall()
    return [r["id"] for r in rows]


# ── boards & columns ─────────────────────────────────────────────────────────

def list_boards(conn: sqlite3.Connection) -> list[dict]:
    boards = []
    for b in conn.execute(
        "SELECT * FROM kanban_boards WHERE archived=0 ORDER BY id"
    ).fetchall():
        board = dict(b)
        board["columns"] = [
            dict(c) for c in conn.execute(
                """
                SELECT c.id, c.name, c.position, c.is_inbox,
                       (SELECT COUNT(*) FROM kanban_cards k
                         WHERE k.column_id=c.id AND k.archived_at IS NULL) AS card_count
                FROM kanban_columns c WHERE c.board_id=? ORDER BY c.position, c.id
                """,
                (b["id"],),
            ).fetchall()
        ]
        board["card_count"] = sum(c["card_count"] for c in board["columns"])
        boards.append(board)
    return boards


def get_board_row(conn: sqlite3.Connection, slug: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM kanban_boards WHERE slug=?", (slug,)).fetchone()
    if row is None:
        raise KanbanError(f"No board with slug '{slug}'.")
    return row


def get_columns(conn: sqlite3.Connection, board_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM kanban_columns WHERE board_id=? ORDER BY position, id", (board_id,)
    ).fetchall()]


def get_board(conn: sqlite3.Connection, slug: str) -> dict:
    """Board with its columns, each holding its non-archived cards in order."""
    board = dict(get_board_row(conn, slug))
    columns = get_columns(conn, board["id"])
    by_col = {c["id"]: c for c in columns}
    for c in columns:
        c["cards"] = []
    for card in conn.execute(
        "SELECT * FROM kanban_cards WHERE board_id=? AND archived_at IS NULL ORDER BY position, id",
        (board["id"],),
    ).fetchall():
        col = by_col.get(card["column_id"])
        if col is not None:
            col["cards"].append(dict(card))
    board["columns"] = columns
    return board


def create_board(conn: sqlite3.Connection, name: str, column_names: Optional[list[str]] = None) -> str:
    name = (name or "").strip()
    if not name:
        raise KanbanError("Board name is required.")
    base = slugify(name)
    slug, n = base, 2
    while conn.execute("SELECT 1 FROM kanban_boards WHERE slug=?", (slug,)).fetchone():
        slug, n = f"{base}-{n}", n + 1
    cur = conn.execute("INSERT INTO kanban_boards (name, slug) VALUES (?,?)", (name, slug))
    cols = [c.strip() for c in (column_names or ["To Do", "Doing", "Done"]) if c.strip()]
    for pos, col in enumerate(cols):
        conn.execute(
            "INSERT INTO kanban_columns (board_id, name, position, is_inbox) VALUES (?,?,?,?)",
            (cur.lastrowid, col, pos, 1 if pos == 0 else 0),
        )
    conn.commit()
    return slug


def inbox_column(conn: sqlite3.Connection, board_id: int) -> dict:
    row = conn.execute(
        "SELECT * FROM kanban_columns WHERE board_id=? ORDER BY is_inbox DESC, position, id LIMIT 1",
        (board_id,),
    ).fetchone()
    if row is None:
        raise KanbanError("Board has no columns.")
    return dict(row)


def find_column(conn: sqlite3.Connection, board_id: int, name: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM kanban_columns WHERE board_id=? AND lower(trim(name))=lower(trim(?))",
        (board_id, name),
    ).fetchone()
    return dict(row) if row else None


def add_column(conn: sqlite3.Connection, board_id: int, name: str, commit: bool = True) -> int:
    name = (name or "").strip()
    if not name:
        raise KanbanError("Column name is required.")
    if find_column(conn, board_id, name):
        raise KanbanError(f"Column '{name}' already exists.")
    pos = conn.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 FROM kanban_columns WHERE board_id=?", (board_id,)
    ).fetchone()[0]
    cur = conn.execute(
        "INSERT INTO kanban_columns (board_id, name, position) VALUES (?,?,?)", (board_id, name, pos)
    )
    if commit:
        conn.commit()
    return cur.lastrowid


def rename_column(conn: sqlite3.Connection, column_id: int, name: str) -> None:
    name = (name or "").strip()
    if not name:
        raise KanbanError("Column name is required.")
    col = _get_column(conn, column_id)
    other = find_column(conn, col["board_id"], name)
    if other and other["id"] != column_id:
        raise KanbanError(f"Column '{name}' already exists.")
    conn.execute("UPDATE kanban_columns SET name=? WHERE id=?", (name, column_id))
    conn.commit()


def shift_column(conn: sqlite3.Connection, column_id: int, direction: int) -> None:
    """Move a column one slot left (-1) or right (+1)."""
    col = _get_column(conn, column_id)
    ids = [c["id"] for c in get_columns(conn, col["board_id"])]
    i = ids.index(column_id)
    j = i + (1 if direction > 0 else -1)
    if 0 <= j < len(ids):
        ids[i], ids[j] = ids[j], ids[i]
        for pos, cid in enumerate(ids):
            conn.execute("UPDATE kanban_columns SET position=? WHERE id=?", (pos, cid))
        conn.commit()


def delete_column(conn: sqlite3.Connection, column_id: int) -> None:
    """Only empty, non-inbox columns can be deleted (archived cards count as contents)."""
    col = _get_column(conn, column_id)
    if col["is_inbox"]:
        raise KanbanError("The inbox column can't be deleted — AI cards land there.")
    if conn.execute("SELECT 1 FROM kanban_cards WHERE column_id=?", (column_id,)).fetchone():
        raise KanbanError(f"Column '{col['name']}' still has cards (including archived ones).")
    conn.execute("DELETE FROM kanban_columns WHERE id=?", (column_id,))
    conn.commit()


def _get_column(conn: sqlite3.Connection, column_id: int) -> dict:
    row = conn.execute("SELECT * FROM kanban_columns WHERE id=?", (column_id,)).fetchone()
    if row is None:
        raise KanbanError(f"No column #{column_id}.")
    return dict(row)


# ── cards ────────────────────────────────────────────────────────────────────

def get_card(conn: sqlite3.Connection, card_id: int) -> dict:
    row = conn.execute(
        """
        SELECT k.*, c.name AS column_name, b.slug AS board_slug
        FROM kanban_cards k
        JOIN kanban_columns c ON c.id = k.column_id
        JOIN kanban_boards b ON b.id = k.board_id
        WHERE k.id=?
        """,
        (card_id,),
    ).fetchone()
    if row is None:
        raise KanbanError(f"No card #{card_id}.")
    return dict(row)


def list_cards(
    conn: sqlite3.Connection,
    board_slug: str,
    column_name: Optional[str] = None,
    include_archived: bool = False,
    limit: int = 200,
) -> list[dict]:
    board = get_board_row(conn, board_slug)
    sql = """
        SELECT k.*, c.name AS column_name
        FROM kanban_cards k JOIN kanban_columns c ON c.id = k.column_id
        WHERE k.board_id=?
    """
    args: list[Any] = [board["id"]]
    if column_name:
        col = find_column(conn, board["id"], column_name)
        if col is None:
            raise KanbanError(f"No column '{column_name}' on board '{board_slug}'.")
        sql += " AND k.column_id=?"
        args.append(col["id"])
    if not include_archived:
        sql += " AND k.archived_at IS NULL"
    sql += " ORDER BY c.position, k.position, k.id LIMIT ?"
    args.append(max(1, min(int(limit), 1000)))
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def _insert_card(conn: sqlite3.Connection, board_id: int, column_id: int,
                 fields: dict[str, Any], created_by: str) -> int:
    key = _dedup_for(fields)
    existing = _find_by_dedup(conn, board_id, key)
    if existing is not None:
        raise DuplicateCardError(existing)
    pos = conn.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 FROM kanban_cards WHERE column_id=? AND archived_at IS NULL",
        (column_id,),
    ).fetchone()[0]
    cols = list(fields)
    cur = conn.execute(
        f"INSERT INTO kanban_cards (board_id, column_id, position, created_by, dedup_key, {', '.join(cols)}) "
        f"VALUES (?,?,?,?,?, {', '.join('?' for _ in cols)})",
        (board_id, column_id, pos, created_by, key, *[fields[c] for c in cols]),
    )
    return cur.lastrowid


def create_card(conn: sqlite3.Connection, board_id: int, data: dict[str, Any],
                column_id: Optional[int] = None, created_by: str = "user") -> int:
    fields = _clean_fields(data, require_title=True)
    if column_id is None:
        column_id = inbox_column(conn, board_id)["id"]
    elif _get_column(conn, column_id)["board_id"] != board_id:
        raise KanbanError("Column belongs to a different board.")
    card_id = _insert_card(conn, board_id, column_id, fields, created_by)
    conn.commit()
    return card_id


def add_cards_batch(
    conn: sqlite3.Connection,
    board_slug: str,
    cards: list[dict[str, Any]],
    *,
    created_by: str = "mcp",
    column_name: Optional[str] = None,
    create_missing_columns: bool = False,
) -> dict:
    """Insert many cards in one transaction. Duplicates (against the board or
    earlier in the same batch) are skipped and reported, never raised.

    Each card may carry a '_column' key overriding column_name (used by the importer).
    """
    board = get_board_row(conn, board_slug)
    default_col = (
        _resolve_column(conn, board["id"], column_name, create_missing_columns)
        if column_name else inbox_column(conn, board["id"])
    )
    added, skipped = [], []
    for raw in cards:
        title = raw.get("title")
        try:
            fields = _clean_fields(raw, require_title=True)
            col = default_col
            if raw.get("_column"):
                col = _resolve_column(conn, board["id"], raw["_column"], create_missing_columns)
            card_id = _insert_card(conn, board["id"], col["id"], fields, created_by)
            added.append({"id": card_id, "title": fields["title"], "column": col["name"]})
        except DuplicateCardError as e:
            skipped.append({"title": title, "reason": "duplicate", "existing_card_id": e.existing_card_id})
        except KanbanError as e:
            skipped.append({"title": title, "reason": str(e)})
    conn.commit()
    return {"added": added, "skipped": skipped}


def _resolve_column(conn: sqlite3.Connection, board_id: int, name: str, create: bool) -> dict:
    col = find_column(conn, board_id, name)
    if col is None:
        if not create:
            raise KanbanError(f"No column '{name}' on this board.")
        col = _get_column(conn, add_column(conn, board_id, name, commit=False))
    return col


def update_card(conn: sqlite3.Connection, card_id: int, data: dict[str, Any]) -> None:
    card = get_card(conn, card_id)
    fields = _clean_fields(data, require_title=False)
    if not fields:
        return
    merged = {**card, **fields}
    key = _dedup_for(merged)
    existing = _find_by_dedup(conn, card["board_id"], key, exclude_card_id=card_id)
    if existing is not None:
        raise DuplicateCardError(existing)
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(
        f"UPDATE kanban_cards SET {sets}, dedup_key=?, updated_at=datetime('now') WHERE id=?",
        (*fields.values(), key, card_id),
    )
    conn.commit()


def move_card(conn: sqlite3.Connection, card_id: int, column_id: int,
              position: Optional[int] = None) -> None:
    """Move a card to column_id at position (None = bottom); renumbers both columns."""
    card = get_card(conn, card_id)
    col = _get_column(conn, column_id)
    if col["board_id"] != card["board_id"]:
        raise KanbanError("Can't move a card to another board's column.")
    target = _column_card_ids(conn, column_id, exclude=card_id)
    pos = len(target) if position is None else max(0, min(int(position), len(target)))
    target.insert(pos, card_id)
    conn.execute(
        "UPDATE kanban_cards SET column_id=?, updated_at=datetime('now') WHERE id=?",
        (column_id, card_id),
    )
    _renumber(conn, column_id, target)
    if card["column_id"] != column_id:
        _renumber(conn, card["column_id"], _column_card_ids(conn, card["column_id"]))
    conn.commit()


def set_archived(conn: sqlite3.Connection, card_id: int, archived: bool) -> None:
    card = get_card(conn, card_id)
    if archived:
        conn.execute(
            "UPDATE kanban_cards SET archived_at=datetime('now'), updated_at=datetime('now') WHERE id=?",
            (card_id,),
        )
        _renumber(conn, card["column_id"], _column_card_ids(conn, card["column_id"]))
    else:
        pos = len(_column_card_ids(conn, card["column_id"]))
        conn.execute(
            "UPDATE kanban_cards SET archived_at=NULL, position=?, updated_at=datetime('now') WHERE id=?",
            (pos, card_id),
        )
    conn.commit()


def inbox_count(conn: sqlite3.Connection, board_slug: str = "job-search") -> int:
    """Non-archived cards in the board's inbox column; 0 if the board is missing."""
    row = conn.execute(
        """
        SELECT COUNT(*) FROM kanban_cards k
        JOIN kanban_columns c ON c.id = k.column_id
        JOIN kanban_boards b ON b.id = k.board_id
        WHERE b.slug=? AND c.is_inbox=1 AND k.archived_at IS NULL
        """,
        (board_slug,),
    ).fetchone()
    return row[0] if row else 0
