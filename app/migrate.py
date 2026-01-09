from __future__ import annotations
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "saltstocks.db"

def column_exists(conn: sqlite3.Connection, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == col for r in rows)

def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    r = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return r is not None

def ensure_column(conn: sqlite3.Connection, table: str, col: str, ddl: str):
    if not column_exists(conn, table, col):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")

def migrate():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")

    with conn:
        # Add new item metadata fields (safe: only adds if missing)
        ensure_column(conn, "items", "company", "company TEXT")
        ensure_column(conn, "items", "category", "category TEXT")
        ensure_column(conn, "items", "brand", "brand TEXT")
        ensure_column(conn, "items", "brand_code", "brand_code TEXT")
        ensure_column(conn, "items", "ip", "ip TEXT")

        # Create counters table
        if not table_exists(conn, "sku_counters"):
            conn.execute("""
            CREATE TABLE sku_counters (
              company TEXT NOT NULL,
              code TEXT NOT NULL,
              next_seq INTEGER NOT NULL DEFAULT 1,
              PRIMARY KEY(company, code)
            )
            """)
        # Create codes table (allowed Brand/Item codes)
        if not table_exists(conn, "codes"):
            conn.execute("""
            CREATE TABLE codes (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              code TEXT NOT NULL UNIQUE,     -- e.g., FUNKO, LEGO, SALT, OIL
              label TEXT NOT NULL,           -- e.g., Funko, LEGO, Epsom Salt
              kind TEXT NOT NULL DEFAULT 'item',  -- 'item' or 'material' (optional use later)
              is_active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL DEFAULT (datetime('now')),
              updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """)

            conn.execute("CREATE INDEX IF NOT EXISTS idx_codes_kind ON codes(kind)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_codes_active ON codes(is_active)")

        # Seed some starter codes if table is empty
        existing_count = conn.execute("SELECT COUNT(*) FROM codes").fetchone()[0]
        if existing_count == 0:
            seed = [
                ("FUNKO", "Funko", "item"),
                ("LEGO", "LEGO", "item"),
                ("NECA", "NECA", "item"),
                ("HASBRO", "Hasbro", "item"),
                ("MISC", "Misc", "item"),
                ("SALT", "Epsom Salt", "material"),
                ("OIL", "Fragrance Oil", "material"),
                ("CTACID", "Citric Acid", "material"),
                ("JAR", "Jar/Container", "material"),
                ("LBL", "Label/Packaging", "material"),
            ]
            conn.executemany(
                "INSERT OR IGNORE INTO codes(code, label, kind) VALUES (?,?,?)",
                seed
            )

        # Ensure SKU uniqueness if possible (existing duplicates would block this)
        # We'll try to add a unique index; if it fails, we won't crash.
        try:
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_items_sku_unique ON items(sku)")
        except sqlite3.IntegrityError:
            pass

        # Backfill company default for existing rows:
        # resale -> GV, material -> UM (reasonable default)
        conn.execute("""
            UPDATE items
            SET company = CASE
              WHEN company IS NULL OR TRIM(company) = '' THEN
                CASE WHEN item_type='resale' THEN 'GV' ELSE 'UM' END
              ELSE company
            END
        """)

        # Backfill brand_code for existing rows if brand is set but brand_code is empty
        conn.execute("""
            UPDATE items
            SET brand_code = UPPER(REPLACE(TRIM(brand), ' ', ''))
            WHERE (brand_code IS NULL OR TRIM(brand_code) = '')
              AND brand IS NOT NULL AND TRIM(brand) != ''
        """)

    conn.close()
    print("Migration complete.")

if __name__ == "__main__":
    migrate()
