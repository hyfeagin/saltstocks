import re
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "saltstocks.db"

SKU_PATTERN = re.compile(r"^(GV|UM)-[A-Z0-9]+-\d{6}$")


def normalize_code(s: str) -> str:
    s = (s or "").strip().upper()
    return s.replace(" ", "").replace("-", "").replace("_", "")


def get_next_sku(conn: sqlite3.Connection, company: str, code: str) -> str:
    company = normalize_code(company) or "GV"
    code = normalize_code(code) or "MISC"

    row = conn.execute(
        "SELECT next_seq FROM sku_counters WHERE company=? AND code=?",
        (company, code),
    ).fetchone()

    if row is None:
        next_seq = 1
        conn.execute(
            "INSERT INTO sku_counters(company, code, next_seq) VALUES(?,?,?)",
            (company, code, 2),
        )
    else:
        next_seq = int(row[0])
        conn.execute(
            "UPDATE sku_counters SET next_seq=? WHERE company=? AND code=?",
            (next_seq + 1, company, code),
        )

    return f"{company}-{code}-{next_seq:06d}"


def main():
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found at: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")

    with conn:
        # Ensure needed columns exist
        # (If these fail, run app/migrate.py first)
        # We won't crash hard; we'll just raise a helpful error.
        try:
            conn.execute("SELECT company, brand_code, sku FROM items LIMIT 1")
            conn.execute("SELECT company, code, next_seq FROM sku_counters LIMIT 1")
        except sqlite3.OperationalError as e:
            raise RuntimeError(
                "Missing schema pieces. Run: python app/migrate.py first, then rerun this script."
            ) from e

        rows = conn.execute(
            """
            SELECT id, sku, company, brand_code
            FROM items
            WHERE item_type='resale'
            ORDER BY id ASC
            """
        ).fetchall()

        changed = 0
        skipped = 0

        for item_id, sku, company, brand_code in rows:
            sku = (sku or "").strip()
            if SKU_PATTERN.match(sku):
                skipped += 1
                continue

            company_n = normalize_code(company) or "GV"
            code_n = normalize_code(brand_code) or "MISC"

            new_sku = get_next_sku(conn, company_n, code_n)
            conn.execute("UPDATE items SET sku=? WHERE id=?", (new_sku, item_id))
            changed += 1

    conn.close()
    print(f"Bulk SKU migration complete. Updated={changed}, Already OK={skipped}")


if __name__ == "__main__":
    main()
