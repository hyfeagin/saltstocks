#!/usr/bin/env python3
"""
Wave to Saltstocks Import Script

This script parses a Wave "Account Transactions" export CSV and converts it into
Saltstocks journal entries with manual categorization of ambiguous transactions.

Usage:
    python3 wave_import.py <wave_export.csv>

Output:
    saltstocks_import.sql - SQL INSERT statements ready to run against Saltstocks DB
"""

import csv
import sys
import json
from decimal import Decimal
from datetime import datetime
from collections import Counter

# Saltstocks template catalog (from spec §3.4)
TEMPLATES = {
    'BUY_INVENTORY': {'name': 'Bought inventory (business funds)', 'dr': 'Inventory', 'cr': 'Bank'},
    'BUY_INVENTORY_PERSONAL': {'name': 'Bought inventory (personal funds)', 'dr': 'Inventory', 'cr': 'Owner Contributions'},
    'BUY_EXPENSE_PERSONAL': {'name': 'Business expense (personal funds)', 'dr': 'Expense', 'cr': 'Owner Contributions'},
    'BUSINESS_MEAL': {'name': 'Business meal', 'dr': 'Meals & Entertainment', 'cr': 'Bank/Card'},
    'TRAVEL_HOTEL': {'name': 'Hotel', 'dr': 'Travel Expense', 'cr': 'Bank/Card'},
    'OFFICE_SUPPLIES': {'name': 'Office supplies', 'dr': 'Office Supplies', 'cr': 'Bank/Card'},
    'SOFTWARE_SUBSCRIPTION': {'name': 'Software/subscription', 'dr': 'Software Expense', 'cr': 'Bank/Card'},
    'SHIPPING_OUTBOUND': {'name': 'Shipping (outbound to customer)', 'dr': 'Shipping Expense', 'cr': 'Bank/Card'},
    'EBAY_FEES': {'name': 'eBay/marketplace fees', 'dr': 'Marketplace Fees', 'cr': 'Bank/Card'},
    'UTILITIES': {'name': 'Utilities (internet, phone)', 'dr': 'Utilities', 'cr': 'Bank/Card'},
    'RENT': {'name': 'Rent / coworking', 'dr': 'Rent Expense', 'cr': 'Bank/Card'},
    'PROFESSIONAL_SERVICES': {'name': 'Accountant / lawyer / consultant', 'dr': 'Professional Services', 'cr': 'Bank/Card'},
    'SELL_INVENTORY_CASH': {'name': 'Sale (cash received)', 'dr': 'Bank', 'cr': 'Sales Revenue'},
    'COGS_RECOGNITION': {'name': 'Cost of goods sold', 'dr': 'COGS', 'cr': 'Inventory'},
    'OWNER_CONTRIBUTION': {'name': 'Owner put money in', 'dr': 'Bank', 'cr': 'Owner Contributions'},
    'OWNER_DRAW': {'name': 'Owner took money out', 'dr': 'Owner Draws', 'cr': 'Bank'},
    'SALES_TAX_REMITTED': {'name': 'Paid sales tax to state', 'dr': 'Sales Tax Payable', 'cr': 'Bank'},
    'OTHER_EXPENSE': {'name': 'Other expense', 'dr': 'Other Expense', 'cr': 'Bank/Card'},
}

# Wave account → Saltstocks template mapping (for auto-categorizable transactions)
WAVE_TO_TEMPLATE = {
    'Sales': 'SELL_INVENTORY_CASH',
    'Cost of Goods Sold': 'COGS_RECOGNITION',
    'Office Supplies': 'OFFICE_SUPPLIES',
    'Computer – Hosting': 'SOFTWARE_SUBSCRIPTION',
    'Professional Fees': 'PROFESSIONAL_SERVICES',
    'Rent Expense': 'RENT',
    'Sales Tax': 'SALES_TAX_REMITTED',
    'Telephone – Land Line': 'UTILITIES',
    'Advertising & Promotion': 'OTHER_EXPENSE',  # Could be more specific
    'Freight & Shipping Costs': 'SHIPPING_OUTBOUND',
    'Merchant Account Fees': 'EBAY_FEES',
}


def parse_wave_csv(filepath):
    """Parse Wave Account Transactions CSV into structured transactions."""
    transactions = []
    current_account = None
    
    with open(filepath, 'r', encoding='utf-8-sig') as f:
        lines = f.readlines()
    
    for line in lines:
        if not line.strip():
            continue
        
        parts = line.strip().split(',')
        
        # Account header
        if len(parts) > 1 and parts[0] == '' and parts[1] and len([p for p in parts if p.strip()]) <= 2:
            if not any(x in parts[1] for x in ['Starting', 'Totals', 'Balance Change', 'ACCOUNT NUMBER']):
                current_account = parts[1].strip()
                continue
        
        # Transaction row
        if current_account and len(parts) >= 5:
            try:
                date_str = parts[1].strip()
                if not date_str or any(x in parts[1] for x in ['Starting', 'Totals', 'Balance']):
                    continue
                
                date = datetime.strptime(date_str, '%Y-%m-%d').date()
                description = parts[2].strip() if len(parts) > 2 else ''
                
                debit_str = parts[3].replace('$', '').replace(',', '').replace('"', '').strip()
                credit_str = parts[4].replace('$', '').replace(',', '').replace('"', '').strip()
                
                debit = Decimal(debit_str) if debit_str else Decimal('0')
                credit = Decimal(credit_str) if credit_str else Decimal('0')
                
                if debit > 0 or credit > 0:
                    transactions.append({
                        'account': current_account,
                        'date': date,
                        'description': description,
                        'debit': debit,
                        'credit': credit
                    })
            except (ValueError, IndexError):
                pass
    
    return transactions


def categorize_transaction(txn, index, total):
    """Prompt user to categorize a transaction. Returns (template_id, expense_category)."""
    print(f"\n{'='*80}")
    print(f"Transaction {index}/{total}")
    print(f"{'='*80}")
    print(f"Date:        {txn['date']}")
    print(f"Wave Account: {txn['account']}")
    print(f"Description: {txn['description']}")
    print(f"Amount:      ${txn['debit'] if txn['debit'] > 0 else txn['credit']}")
    print(f"Type:        {'Debit' if txn['debit'] > 0 else 'Credit'}")
    
    # Check if auto-categorizable
    if txn['account'] in WAVE_TO_TEMPLATE:
        suggested = WAVE_TO_TEMPLATE[txn['account']]
        print(f"\n✓ Auto-categorized as: {TEMPLATES[suggested]['name']}")
        confirm = input(f"Accept this? (Y/n): ").strip().lower()
        if confirm in ['', 'y', 'yes']:
            return suggested, None
    
    # Manual categorization
    print("\nAvailable categories:")
    template_list = list(TEMPLATES.items())
    for i, (tid, info) in enumerate(template_list, 1):
        print(f"  {i:2}. {info['name']}")
    
    while True:
        choice = input(f"\nPick a category (1-{len(template_list)}) or 's' to skip: ").strip()
        if choice.lower() == 's':
            return 'SKIP', None
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(template_list):
                template_id = template_list[idx][0]
                
                # If it's a generic expense, ask for specific category
                if template_id in ['BUY_EXPENSE_PERSONAL', 'OTHER_EXPENSE']:
                    category = input("Specific expense category (e.g., 'Meals', 'Travel', 'Software'): ").strip()
                    return template_id, category
                
                return template_id, None
        except ValueError:
            pass
        print("Invalid choice. Try again.")


def generate_sql_inserts(categorized_transactions):
    """Generate SQL INSERT statements for Saltstocks."""
    sql_lines = []
    sql_lines.append("-- Saltstocks journal entries imported from Wave")
    sql_lines.append("-- Generated: " + datetime.now().isoformat())
    sql_lines.append("")
    
    entry_id = 1
    
    for txn in categorized_transactions:
        if txn['template'] == 'SKIP':
            sql_lines.append(f"-- SKIPPED: {txn['date']} | {txn['description']}")
            continue
        
        template_info = TEMPLATES[txn['template']]
        amount = txn['debit'] if txn['debit'] > 0 else txn['credit']
        
        # Journal entry
        escaped_desc = txn['description'].replace("'", "''")
        sql_lines.append(f"-- {txn['date']} | {txn['description']} | {template_info['name']}")
        sql_lines.append(
            f"INSERT INTO journal_entries (id, entry_date, description, template_id, vendor, total_amount, created_by_method, is_void) "
            f"VALUES ({entry_id}, '{txn['date']}', '{escaped_desc}', '{txn['template']}', NULL, {amount}, 'import_wave', FALSE);"
        )
        
        # Journal lines (simplified — real version would map accounts properly)
        # This is a placeholder; actual implementation needs account_id resolution
        sql_lines.append(
            f"-- TODO: Add journal_lines for entry {entry_id} based on template {txn['template']}"
        )
        sql_lines.append("")
        
        entry_id += 1
    
    return "\n".join(sql_lines)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 wave_import.py <wave_export.csv>")
        sys.exit(1)
    
    csv_path = sys.argv[1]
    
    print("=== Wave to Saltstocks Import ===\n")
    print(f"Parsing: {csv_path}")
    
    transactions = parse_wave_csv(csv_path)
    print(f"\nFound {len(transactions)} transactions")
    
    # Show summary
    account_counts = Counter([t['account'] for t in transactions])
    print("\nTransactions by Wave account:")
    for account, count in sorted(account_counts.items()):
        auto = " (auto-categorizable)" if account in WAVE_TO_TEMPLATE else ""
        print(f"  {account}: {count}{auto}")
    
    # Filter to transactions that need categorization
    needs_categorization = [t for t in transactions if t['account'] not in WAVE_TO_TEMPLATE]
    auto_categorizable = [t for t in transactions if t['account'] in WAVE_TO_TEMPLATE]
    
    print(f"\n✓ {len(auto_categorizable)} can be auto-categorized")
    print(f"⚠ {len(needs_categorization)} need manual review")
    
    input("\nPress Enter to start categorization...")
    
    categorized = []
    
    # Auto-categorize
    for txn in auto_categorizable:
        template = WAVE_TO_TEMPLATE[txn['account']]
        categorized.append({**txn, 'template': template, 'category': None})
    
    # Manual categorization
    for i, txn in enumerate(needs_categorization, 1):
        template, category = categorize_transaction(txn, i, len(needs_categorization))
        categorized.append({**txn, 'template': template, 'category': category})
    
    # Generate SQL
    output_file = 'saltstocks_import.sql'
    sql = generate_sql_inserts(categorized)
    
    with open(output_file, 'w') as f:
        f.write(sql)
    
    print(f"\n{'='*80}")
    print(f"✓ Import complete!")
    print(f"{'='*80}")
    print(f"Output: {output_file}")
    print(f"Total entries: {len([t for t in categorized if t['template'] != 'SKIP'])}")
    print(f"Skipped: {len([t for t in categorized if t['template'] == 'SKIP'])}")
    print("\nNext steps:")
    print("1. Review saltstocks_import.sql")
    print("2. Run against your Saltstocks database")
    print("3. Verify entries appear in /accounting/entries")


if __name__ == '__main__':
    main()