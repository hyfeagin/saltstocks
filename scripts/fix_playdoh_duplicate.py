"""
One-time data fix:
  1. Delete item #91 (Black Panther Play-Doh — duplicate created by mistake)
  2. Backlink journal entry #109's DR 1200 line to item #31 (Play-Doh Marvel Black Panther
     Cutting Claws — the correct item), WITHOUT changing qty or posting new entries.

Run on the VPS:
  cd ~/saltstocks && source .venv/bin/activate && python scripts/fix_playdoh_duplicate.py
"""
import sqlite3
from pathlib import Path

DB = Path(__file__).parent.parent / "data" / "saltstocks.db"

DUPLICATE_ITEM_ID = 91
CORRECT_ITEM_ID   = 31
ENTRY_ID          = 109


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    # ── Verify items ──────────────────────────────────────────────────────────
    dup = conn.execute("SELECT * FROM items WHERE id = ?", (DUPLICATE_ITEM_ID,)).fetchone()
    if dup is None:
        print(f"ERROR: item #{DUPLICATE_ITEM_ID} not found.")
        return
    print(f"Duplicate item #{DUPLICATE_ITEM_ID}: '{dup['name']}' qty={dup['qty_on_hand']} sku={dup['sku']}")

    correct = conn.execute("SELECT * FROM items WHERE id = ?", (CORRECT_ITEM_ID,)).fetchone()
    if correct is None:
        print(f"ERROR: item #{CORRECT_ITEM_ID} not found.")
        return
    print(f"Correct item  #{CORRECT_ITEM_ID}: '{correct['name']}' qty={correct['qty_on_hand']} sku={correct['sku']}")

    # ── Verify JE #109 ────────────────────────────────────────────────────────
    je = conn.execute("SELECT * FROM journal_entries WHERE id = ?", (ENTRY_ID,)).fetchone()
    if je is None:
        print(f"ERROR: journal_entry #{ENTRY_ID} not found.")
        return
    print(f"\nJE #{ENTRY_ID}: {je['entry_date']} | {je['description']} | ${je['total_amount']}")

    # ── Check for references to duplicate item ────────────────────────────────
    print(f"\nChecking references to item #{DUPLICATE_ITEM_ID}...")

    jl_refs = conn.execute(
        "SELECT id, entry_id, debit, credit FROM journal_lines WHERE inventory_item_id = ?",
        (DUPLICATE_ITEM_ID,),
    ).fetchall()
    print(f"  journal_lines referencing #{DUPLICATE_ITEM_ID}: {len(jl_refs)}")
    for r in jl_refs:
        print(f"    line_id={r['id']} entry_id={r['entry_id']} debit={r['debit']} credit={r['credit']}")

    resale_refs = conn.execute(
        "SELECT id FROM resale_listings WHERE item_id = ?", (DUPLICATE_ITEM_ID,)
    ).fetchall()
    print(f"  resale_listings referencing #{DUPLICATE_ITEM_ID}: {len(resale_refs)}")

    # ── Inspect DR 1200 lines for JE #109 ────────────────────────────────────
    inv_acct = conn.execute("SELECT id FROM accounts WHERE code = '1200'").fetchone()
    if inv_acct is None:
        print("ERROR: account 1200 not found.")
        return

    dr_lines = conn.execute(
        """
        SELECT id, debit, inventory_item_id
        FROM journal_lines
        WHERE entry_id = ? AND account_id = ? AND CAST(debit AS REAL) > 0
        ORDER BY id
        """,
        (ENTRY_ID, inv_acct["id"]),
    ).fetchall()

    print(f"\nDR lines on account 1200 for JE #{ENTRY_ID}:")
    for ln in dr_lines:
        print(f"  line_id={ln['id']} debit={ln['debit']} inventory_item_id={ln['inventory_item_id']}")

    if not dr_lines:
        print("ERROR: no DR lines on 1200 found for this entry — nothing to backlink.")
        return

    # ── Preview changes ───────────────────────────────────────────────────────
    print(f"\nPlanned changes:")
    print(f"  1. DELETE items WHERE id={DUPLICATE_ITEM_ID} ('{dup['name']}')")
    for ln in dr_lines:
        print(f"  2. UPDATE journal_lines SET inventory_item_id={CORRECT_ITEM_ID}"
              f" WHERE id={ln['id']}  (JE #{ENTRY_ID} DR line, was {ln['inventory_item_id']})")
    print(f"  qty_on_hand for item #{CORRECT_ITEM_ID} will NOT change (stays {correct['qty_on_hand']})")

    if resale_refs:
        print(f"\nWARNING: {len(resale_refs)} resale_listing row(s) reference item #{DUPLICATE_ITEM_ID}.")
        print("  These will be deleted as part of the item deletion.")

    confirm = input("\nApply? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    # ── Apply ─────────────────────────────────────────────────────────────────
    with conn:
        # Remove any journal_line links pointing to the duplicate
        conn.execute(
            "UPDATE journal_lines SET inventory_item_id = NULL WHERE inventory_item_id = ?",
            (DUPLICATE_ITEM_ID,),
        )
        # Remove resale listings for the duplicate
        conn.execute("DELETE FROM resale_listings WHERE item_id = ?", (DUPLICATE_ITEM_ID,))
        # Delete the duplicate item
        conn.execute("DELETE FROM items WHERE id = ?", (DUPLICATE_ITEM_ID,))

        # Backlink JE #109 DR lines to the correct item
        for ln in dr_lines:
            conn.execute(
                "UPDATE journal_lines SET inventory_item_id = ? WHERE id = ?",
                (CORRECT_ITEM_ID, ln["id"]),
            )

    # ── Verify ────────────────────────────────────────────────────────────────
    still_exists = conn.execute(
        "SELECT id FROM items WHERE id = ?", (DUPLICATE_ITEM_ID,)
    ).fetchone()
    print(f"\nItem #{DUPLICATE_ITEM_ID} deleted: {still_exists is None}")

    updated_lines = conn.execute(
        """
        SELECT jl.id, jl.debit, jl.inventory_item_id, i.name
        FROM journal_lines jl
        LEFT JOIN items i ON i.id = jl.inventory_item_id
        WHERE jl.entry_id = ? AND jl.account_id = ? AND CAST(jl.debit AS REAL) > 0
        """,
        (ENTRY_ID, inv_acct["id"]),
    ).fetchall()
    print(f"JE #{ENTRY_ID} DR lines after fix:")
    for ln in updated_lines:
        print(f"  line_id={ln['id']} debit={ln['debit']} → item #{ln['inventory_item_id']} '{ln['name']}'")

    qty_unchanged = conn.execute(
        "SELECT qty_on_hand FROM items WHERE id = ?", (CORRECT_ITEM_ID,)
    ).fetchone()
    print(f"Item #{CORRECT_ITEM_ID} qty_on_hand (unchanged): {qty_unchanged['qty_on_hand']}")

    print("\nDone.")
    conn.close()


if __name__ == "__main__":
    main()
