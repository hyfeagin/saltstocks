PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_type TEXT NOT NULL CHECK(item_type IN ('resale','material')),
  sku TEXT,
  name TEXT NOT NULL,
  unit TEXT NOT NULL DEFAULT 'each',          -- 'each' for resale, 'oz' for materials later
  qty_on_hand REAL NOT NULL DEFAULT 0,        -- supports multiples and fractional (for oz later)
  unit_cost REAL NOT NULL DEFAULT 0,          -- cost per unit (each or oz)
  location TEXT,
  condition TEXT,
  tags TEXT,
  notes TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_items_type ON items(item_type);
CREATE INDEX IF NOT EXISTS idx_items_sku ON items(sku);

CREATE TABLE IF NOT EXISTS resale_listings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id INTEGER NOT NULL,
  channel TEXT NOT NULL DEFAULT 'unassigned', -- ebay, etsy, shopify, local, etc.
  status TEXT NOT NULL DEFAULT 'unlisted',    -- unlisted, listed, sold, donated, trashed
  list_price REAL,
  url TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_resale_item_id ON resale_listings(item_id);

CREATE TABLE IF NOT EXISTS categories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TRIGGER IF NOT EXISTS trg_items_updated_at
AFTER UPDATE ON items
FOR EACH ROW
BEGIN
  UPDATE items SET updated_at = datetime('now') WHERE id = NEW.id;
END;
