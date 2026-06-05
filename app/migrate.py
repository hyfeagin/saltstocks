

##  Inline Comment for `migrate.py`

##Add this near the top of `app/migrate.py` so Future Holly sees it **before** she forgets:

#```python
# IMPORTANT:
# Any schema or seed change requires running:
#   source .venv/bin/activate
#   python app/migrate.py
#
# If UI looks broken after pulling code, this is the first thing to run.

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

        # Create categories table
        if not table_exists(conn, "categories"):
            conn.execute("""
            CREATE TABLE categories (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL UNIQUE,
              is_active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL DEFAULT (datetime('now')),
              updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """)

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

        # Seed categories if table is empty
        category_count = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
        if category_count == 0:
            category_seed = [
                ("Collectibles",),
                ("Toys",),
                ("Decor",),
                ("Books/Media",),
                ("Clothing",),
                ("Soap/Bath & Beauty",),
            ]
            conn.executemany(
                "INSERT OR IGNORE INTO categories(name) VALUES (?)",
                category_seed,
            )

        # eBay settings (single-row table)
        if not table_exists(conn, "ebay_settings"):
            conn.execute("""
            CREATE TABLE ebay_settings (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              client_id TEXT,
              client_secret TEXT,
              environment TEXT NOT NULL DEFAULT 'SANDBOX'
                CHECK (environment IN ('PRODUCTION', 'SANDBOX')),
              refresh_token TEXT,
              updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """)

        # eBay credentials by environment (supports separate sandbox/prod keys)
        if not table_exists(conn, "ebay_credentials"):
            conn.execute("""
            CREATE TABLE ebay_credentials (
              environment TEXT PRIMARY KEY
                CHECK (environment IN ('PRODUCTION', 'SANDBOX')),
              client_id TEXT,
              client_secret TEXT,
              refresh_token TEXT,
              ru_name TEXT,
              updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """)
        ensure_column(conn, "ebay_credentials", "ru_name", "ru_name TEXT")

        # Active environment selector (which credential set should be used)
        if not table_exists(conn, "ebay_state"):
            conn.execute("""
            CREATE TABLE ebay_state (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              active_environment TEXT NOT NULL DEFAULT 'SANDBOX'
                CHECK (active_environment IN ('PRODUCTION', 'SANDBOX')),
              updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """)
            conn.execute(
                "INSERT OR IGNORE INTO ebay_state (id, active_environment) VALUES (1, 'SANDBOX')"
            )

        # Backfill from legacy ebay_settings single row to new profile/state tables.
        if table_exists(conn, "ebay_settings"):
            legacy = conn.execute(
                """
                SELECT client_id, client_secret, refresh_token, environment
                FROM ebay_settings
                WHERE id=1
                """
            ).fetchone()
            if legacy:
                legacy_env = (legacy[3] or "SANDBOX").upper()
                if legacy_env not in {"PRODUCTION", "SANDBOX"}:
                    legacy_env = "SANDBOX"

                conn.execute(
                    """
                    INSERT INTO ebay_credentials (environment, client_id, client_secret, refresh_token, updated_at)
                    VALUES (?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(environment) DO UPDATE SET
                      client_id=COALESCE(NULLIF(excluded.client_id,''), ebay_credentials.client_id),
                      client_secret=COALESCE(NULLIF(excluded.client_secret,''), ebay_credentials.client_secret),
                      refresh_token=COALESCE(NULLIF(excluded.refresh_token,''), ebay_credentials.refresh_token),
                      updated_at=datetime('now')
                    """,
                    (
                        legacy_env,
                        legacy[0] or "",
                        legacy[1] or "",
                        legacy[2] or "",
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO ebay_state (id, active_environment, updated_at)
                    VALUES (1, ?, datetime('now'))
                    ON CONFLICT(id) DO UPDATE SET
                      active_environment=excluded.active_environment,
                      updated_at=datetime('now')
                    """,
                    (legacy_env,),
                )

        # eBay import idempotency log
        if not table_exists(conn, "ebay_import_log"):
            conn.execute("""
            CREATE TABLE ebay_import_log (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              order_id TEXT NOT NULL,
              line_item_id TEXT NOT NULL,
              sku TEXT,
              qty REAL NOT NULL,
              imported_at TEXT NOT NULL DEFAULT (datetime('now')),
              item_id INTEGER,
              FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE SET NULL,
              UNIQUE(order_id, line_item_id)
            )
            """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ebay_import_log_sku ON ebay_import_log(sku)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ebay_import_log_item_id ON ebay_import_log(item_id)"
        )

        # Ensure SKU uniqueness if possible (existing duplicates would block this)
        # We'll try to add a unique index; if it fails, we won't crash.
        try:
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_items_sku_unique ON items(sku)")
        except sqlite3.IntegrityError:
            pass

        # ── Accounting: Chart of Accounts ─────────────────────────────────────
        if not table_exists(conn, "accounts"):
            conn.execute("""
            CREATE TABLE accounts (
              id                     INTEGER PRIMARY KEY AUTOINCREMENT,
              code                   TEXT NOT NULL UNIQUE,
              name                   TEXT NOT NULL,
              type                   TEXT NOT NULL
                CHECK (type IN ('asset','liability','equity','income','expense')),
              subtype                TEXT,
              is_active              INTEGER NOT NULL DEFAULT 1,
              is_personal_funds_proxy INTEGER NOT NULL DEFAULT 0,
              is_system_protected    INTEGER NOT NULL DEFAULT 0,
              created_at             TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_accounts_type ON accounts(type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_accounts_active ON accounts(is_active)")

        # Seed default chart of accounts (skip if any rows exist)
        if conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0:
            # (code, name, type, subtype, is_personal_funds_proxy, is_system_protected)
            accounts_seed = [
                ("1010", "Cash on Hand",               "asset",     "cash",         0, 0),
                ("1020", "Bank — Primary Checking",    "asset",     "bank",         0, 0),
                ("1030", "Bank — Business Savings",    "asset",     "bank",         0, 0),
                ("1200", "Inventory",                  "asset",     "inventory",    0, 1),
                ("1500", "Receipt Vault Holding",      "asset",     "clearing",     0, 0),
                ("2010", "Credit Card — Primary",      "liability", "credit_card",  0, 0),
                ("2100", "Sales Tax Payable",          "liability", "tax",          0, 1),
                ("3000", "Owner's Equity",             "equity",    None,           0, 0),
                ("3100", "Owner Contributions",        "equity",    None,           1, 1),
                ("3200", "Owner Draws",                "equity",    None,           0, 1),
                ("4000", "Sales Revenue",              "income",    None,           0, 0),
                ("4100", "Other Income",               "income",    None,           0, 0),
                ("5000", "Cost of Goods Sold",         "expense",   "cogs",         0, 1),
                ("6010", "Meals & Entertainment",      "expense",   None,           0, 0),
                ("6020", "Travel",                     "expense",   None,           0, 0),
                ("6030", "Office Supplies",            "expense",   None,           0, 0),
                ("6040", "Software & Subscriptions",   "expense",   None,           0, 0),
                ("6050", "Shipping",                   "expense",   None,           0, 0),
                ("6060", "Marketplace Fees",           "expense",   None,           0, 0),
                ("6070", "Payment Processing Fees",    "expense",   None,           0, 0),
                ("6080", "Utilities",                  "expense",   None,           0, 0),
                ("6090", "Rent",                       "expense",   None,           0, 0),
                ("6100", "Professional Services",      "expense",   None,           0, 0),
                ("6110", "Bank Fees",                  "expense",   None,           0, 0),
                ("6900", "Other Expenses",             "expense",   None,           0, 0),
            ]
            conn.executemany(
                """
                INSERT OR IGNORE INTO accounts
                  (code, name, type, subtype, is_personal_funds_proxy, is_system_protected)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                accounts_seed,
            )

        # ── Accounting: Journal Lines ─────────────────────────────────────────
        if not table_exists(conn, "journal_lines"):
            conn.execute("""
            CREATE TABLE journal_lines (
              id                 INTEGER PRIMARY KEY AUTOINCREMENT,
              entry_id           INTEGER NOT NULL
                REFERENCES journal_entries(id) ON DELETE CASCADE,
              account_id         INTEGER NOT NULL
                REFERENCES accounts(id),
              debit              TEXT NOT NULL DEFAULT '0',
              credit             TEXT NOT NULL DEFAULT '0',
              memo               TEXT,
              inventory_item_id  INTEGER
                REFERENCES items(id) ON DELETE SET NULL,
              CHECK (
                (CAST(debit AS REAL) > 0 AND CAST(credit AS REAL) = 0)
                OR
                (CAST(debit AS REAL) = 0 AND CAST(credit AS REAL) > 0)
              )
            )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_journal_lines_entry ON journal_lines(entry_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_journal_lines_account ON journal_lines(account_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_journal_lines_item ON journal_lines(inventory_item_id)"
            )

        # ── Accounting: Receipts ─────────────────────────────────────────────
        if not table_exists(conn, "receipts"):
            conn.execute("""
            CREATE TABLE receipts (
              id                INTEGER PRIMARY KEY AUTOINCREMENT,
              entry_id          INTEGER NOT NULL
                REFERENCES journal_entries(id) ON DELETE CASCADE,
              original_filename TEXT NOT NULL,
              stored_path       TEXT NOT NULL UNIQUE,
              mime_type         TEXT NOT NULL,
              file_size_bytes   INTEGER NOT NULL,
              sha256            TEXT NOT NULL,
              uploaded_at       TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_receipts_entry ON receipts(entry_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_receipts_sha256 ON receipts(sha256)"
            )

        # ── Accounting: Sales Tax Rates ──────────────────────────────────────
        if not table_exists(conn, "sales_tax_rates"):
            conn.execute("""
            CREATE TABLE sales_tax_rates (
              id               INTEGER PRIMARY KEY AUTOINCREMENT,
              jurisdiction     TEXT NOT NULL,
              rate             TEXT NOT NULL,
              is_default       INTEGER NOT NULL DEFAULT 0,
              effective_from   TEXT NOT NULL,
              effective_to     TEXT
            )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sales_tax_rates_default ON sales_tax_rates(is_default)"
            )

        # Seed NC rate if table is empty
        if conn.execute("SELECT COUNT(*) FROM sales_tax_rates").fetchone()[0] == 0:
            conn.execute(
                """
                INSERT INTO sales_tax_rates (jurisdiction, rate, is_default, effective_from)
                VALUES (?, ?, 1, ?)
                """,
                ("North Carolina", "0.0475", "2024-01-01"),
            )

        # ── Accounting: Journal Entries ───────────────────────────────────────
        if not table_exists(conn, "journal_entries"):
            conn.execute("""
            CREATE TABLE journal_entries (
              id                 INTEGER PRIMARY KEY AUTOINCREMENT,
              entry_date         TEXT NOT NULL,
              description        TEXT NOT NULL,
              template_id        TEXT NOT NULL,
              total_amount       TEXT NOT NULL,
              created_at         TEXT NOT NULL DEFAULT (datetime('now')),
              created_by_method  TEXT NOT NULL DEFAULT 'questionnaire'
                CHECK (created_by_method IN ('questionnaire','manual','import_ebay','system_auto')),
              vendor             TEXT,
              notes              TEXT,
              is_void            INTEGER NOT NULL DEFAULT 0,
              void_reason        TEXT,
              void_at            TEXT
            )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_journal_entries_date ON journal_entries(entry_date)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_journal_entries_template ON journal_entries(template_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_journal_entries_void ON journal_entries(is_void)"
            )

        # ── Accounting: Questionnaire Sessions ───────────────────────────────
        if not table_exists(conn, "questionnaire_sessions"):
            conn.execute("""
            CREATE TABLE questionnaire_sessions (
              session_id  TEXT PRIMARY KEY,
              data        TEXT NOT NULL,
              created_at  TEXT NOT NULL DEFAULT (datetime('now')),
              updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """)

        # ── App Settings (key/value store) ───────────────────────────────────
        if not table_exists(conn, "app_settings"):
            conn.execute("""
            CREATE TABLE app_settings (
              key         TEXT PRIMARY KEY,
              value       TEXT NOT NULL DEFAULT '',
              updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """)

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

        # ── Inventory Lots ────────────────────────────────────────────────────
        if not table_exists(conn, "inventory_lots"):
            conn.execute("""
            CREATE TABLE inventory_lots (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                journal_entry_id INTEGER REFERENCES journal_entries(id) ON DELETE SET NULL,
                description      TEXT NOT NULL,
                qty_received     INTEGER NOT NULL,
                qty_remaining    INTEGER NOT NULL,
                unit_cost        TEXT NOT NULL,
                received_date    TEXT NOT NULL,
                vendor           TEXT,
                notes            TEXT,
                created_at       TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_lots_entry ON inventory_lots(journal_entry_id)"
            )

        ensure_column(
            conn, "items", "lot_id",
            "lot_id INTEGER REFERENCES inventory_lots(id) ON DELETE SET NULL"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_items_lot ON items(lot_id)")

    conn.close()
    print("Migration complete.")

if __name__ == "__main__":
    migrate()
