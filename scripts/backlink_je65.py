"""
One-time data fix: backlink journal entry #65 to inventory items #39 and #40.

Pattern: journal_lines.inventory_item_id links a debit on account 1200 to the item
it represents. For purchase_entries to show up on an item, the DR 1200 line for that
item's cost must have inventory_item_id set.

JE #65 was created before items 39/40 were linked, so their inventory_item_id is NULL.

Run on the VPS:
  cd ~/saltstocks && source .venv/bin/activate && python scripts/backlink_je65.py
"""
import sqlite3
from decimal import Decimal
from pathlib import Path

DB = Path(__file__).parent.parent / "data" / "saltstocks.db"

ENTRY_ID = 65
ITEM_39_ID = 39   # Yahaba 1410,      qty=2
ITEM_40_ID = 40   # Hiyori Sarugaki,  qty=3


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    # --- Verify JE exists ---
    je = conn.execute(
        "SELECT * FROM journal_entries WHERE id = ?", (ENTRY_ID,)
    ).fetchone()
    if je is None:
        print(f"ERROR: journal_entry #{ENTRY_ID} not found.")
        return
    print(f"JE #{ENTRY_ID}: {je['entry_date']} | {je['description']} | ${je['total_amount']}")

    # --- Verify items exist ---
    item39 = conn.execute("SELECT * FROM items WHERE id = ?", (ITEM_39_ID,)).fetchone()
    item40 = conn.execute("SELECT * FROM items WHERE id = ?", (ITEM_40_ID,)).fetchone()
    if item39 is None or item40 is None:
        print(f"ERROR: item 39 or 40 not found.")
        return
    print(f"Item {ITEM_39_ID}: {item39['name']} qty={item39['qty_on_hand']} unit_cost={item39['unit_cost']}")
    print(f"Item {ITEM_40_ID}: {item40['name']} qty={item40['qty_on_hand']} unit_cost={item40['unit_cost']}")

    # --- Inspect existing DR lines on account 1200 ---
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

    print(f"\nExisting DR lines on 1200 for JE #{ENTRY_ID}: {len(dr_lines)}")
    for ln in dr_lines:
        print(f"  line_id={ln['id']} debit={ln['debit']} inventory_item_id={ln['inventory_item_id']}")

    # --- Guard: if already linked, report and exit ---
    already_linked = [ln for ln in dr_lines if ln["inventory_item_id"] is not None]
    if already_linked:
        print("\nLines already have inventory_item_id set — nothing to do.")
        return

    # --- Compute cost split ---
    cost39 = Decimal(str(item39["unit_cost"])) * Decimal(str(int(item39["qty_on_hand"])))
    cost40 = Decimal(str(item40["unit_cost"])) * Decimal(str(int(item40["qty_on_hand"])))
    total_cost = cost39 + cost40
    print(f"\nCost split: item {ITEM_39_ID} = {cost39}, item {ITEM_40_ID} = {cost40}, total = {total_cost}")

    if len(dr_lines) == 0:
        print("ERROR: no DR lines on 1200 found for this entry.")
        return

    elif len(dr_lines) == 1:
        # Single combined line — split into two, preserving the total
        original_line_id = dr_lines[0]["id"]
        original_amount = Decimal(str(dr_lines[0]["debit"]))
        print(f"\nCASE: one combined DR line (${original_amount}) — splitting into two.")

        # Scale the split to match the actual JE amount (in case unit costs drifted)
        if total_cost > 0:
            amt39 = (cost39 / total_cost * original_amount).quantize(Decimal("0.01"))
        else:
            amt39 = original_amount / 2
        amt40 = original_amount - amt39  # absorb rounding into item 40

        print(f"  Will set line {original_line_id}: debit={amt39}, inventory_item_id={ITEM_39_ID}")
        print(f"  Will insert new line:            debit={amt40}, inventory_item_id={ITEM_40_ID}")

        confirm = input("\nApply? [y/N] ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            return

        with conn:
            conn.execute(
                "UPDATE journal_lines SET debit=?, inventory_item_id=? WHERE id=?",
                (str(amt39), ITEM_39_ID, original_line_id),
            )
            conn.execute(
                """
                INSERT INTO journal_lines (entry_id, account_id, debit, credit, memo, inventory_item_id)
                VALUES (?, ?, ?, '0', NULL, ?)
                """,
                (ENTRY_ID, inv_acct["id"], str(amt40), ITEM_40_ID),
            )

    elif len(dr_lines) == 2:
        # Already two DR lines — just set inventory_item_id on each
        print(f"\nCASE: two DR lines found — assigning inventory_item_id directly.")
        # Assign by cost proximity
        def closest(line, c1, c2, id1, id2):
            d1 = abs(Decimal(str(line["debit"])) - c1)
            d2 = abs(Decimal(str(line["debit"])) - c2)
            return id1 if d1 <= d2 else id2

        assignments = []
        for ln in dr_lines:
            iid = closest(ln, cost39, cost40, ITEM_39_ID, ITEM_40_ID)
            assignments.append((ln["id"], iid))
            print(f"  line_id={ln['id']} debit={ln['debit']} → inventory_item_id={iid}")

        confirm = input("\nApply? [y/N] ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            return

        with conn:
            for line_id, iid in assignments:
                conn.execute(
                    "UPDATE journal_lines SET inventory_item_id=? WHERE id=?",
                    (iid, line_id),
                )

    else:
        print(f"UNEXPECTED: {len(dr_lines)} DR lines on 1200 — manual review needed.")
        return

    # --- Verify ---
    print("\n=== Verification: DR lines on 1200 after fix ===")
    for ln in conn.execute(
        """
        SELECT jl.id, jl.debit, jl.inventory_item_id, i.name
        FROM journal_lines jl
        LEFT JOIN items i ON i.id = jl.inventory_item_id
        WHERE jl.entry_id = ? AND jl.account_id = ? AND CAST(jl.debit AS REAL) > 0
        ORDER BY jl.id
        """,
        (ENTRY_ID, inv_acct["id"]),
    ).fetchall():
        print(f"  line_id={ln['id']} debit={ln['debit']} item_id={ln['inventory_item_id']} name={ln['name']}")

    print("\nDone.")
    conn.close()


if __name__ == "__main__":
    main()
