# Import Syngenta CSV data into SQLite
import csv
import sqlite3
import os
from config import Config

DB_PATH = Config.DATABASE_PATH
SYNGENTA_DIR = os.path.join(os.path.dirname(__file__), 'data', 'syngenta')


def import_syngenta():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # ═══════════════════════════════════════
    # TABLE: SKU Product Mapping
    # ═══════════════════════════════════════
    c.execute("""
    CREATE TABLE IF NOT EXISTS sku_product_map (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sku_name TEXT NOT NULL UNIQUE,
        our_product TEXT NOT NULL,
        product_category TEXT DEFAULT 'fungicide'
    )""")
    
    sku_map = {
        'Amistar 250 SC': ('Amistar Top', 'fungicide'),
        'Score 250 EC': ('Score', 'fungicide'),
        'Tilt 250 EC': ('Tilt', 'fungicide'),
        'Actara 25 WG': ('Actara', 'insecticide'),
        'Kavach 75 WP': ('Kavach', 'fungicide'),
        'Alto 5 SC': ('Alto', 'fungicide'),
        'Vertimec 1.8 EC': ('Vertimec', 'insecticide'),
        'Vibrance Integral': ('Vibrance Duo', 'seed_treatment'),
        'Axial 50 EC': ('Axial', 'herbicide'),
        'Cruiser 350 FS': ('Cruiser', 'seed_treatment'),
        'Topik 15 WP': ('Topik', 'herbicide'),
        'Movondo': ('Movondo', 'fungicide'),
    }
    for sku, (prod, cat) in sku_map.items():
        c.execute("INSERT OR IGNORE INTO sku_product_map (sku_name, our_product, product_category) VALUES (?,?,?)",
                  (sku, prod, cat))
    print(f"[IMPORT] SKU mapping: {len(sku_map)} entries")
    
    # ═══════════════════════════════════════
    # TABLE: Syngenta Retailers
    # ═══════════════════════════════════════
    c.execute("""
    CREATE TABLE IF NOT EXISTS syngenta_retailers (
        retailer_id TEXT PRIMARY KEY,
        territory_id TEXT,
        state TEXT,
        district TEXT,
        tehsil TEXT
    )""")
    
    path = os.path.join(SYNGENTA_DIR, 'retailers.csv')
    if os.path.exists(path):
        with open(path, encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f)
            rows = 0
            for r in reader:
                c.execute("""INSERT OR REPLACE INTO syngenta_retailers 
                    (retailer_id, territory_id, state, district, tehsil)
                    VALUES (?,?,?,?,?)""",
                    (r['retailer_id'], r['territory_id'], r['state'], r['district'], r['tehsil']))
                rows += 1
        print(f"[IMPORT] Retailers: {rows}")
    
    # ═══════════════════════════════════════
    # TABLE: Inventory (weekly stock)
    # ═══════════════════════════════════════
    c.execute("""
    CREATE TABLE IF NOT EXISTS syngenta_inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        retailer_id TEXT,
        sku_id TEXT,
        sku_name TEXT,
        sku_qty INTEGER,
        week_end_date TEXT,
        UNIQUE(retailer_id, sku_id, week_end_date)
    )""")
    
    path = os.path.join(SYNGENTA_DIR, 'retailer_inventory_weekly.csv')
    if os.path.exists(path):
        with open(path, encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f)
            rows = 0
            batch = []
            for r in reader:
                batch.append((r['retailer_id'], r['sku_id'], r['sku_name'],
                              int(r['sku_qty']), r['week_end_date']))
                if len(batch) >= 5000:
                    c.executemany("""INSERT OR REPLACE INTO syngenta_inventory 
                        (retailer_id, sku_id, sku_name, sku_qty, week_end_date)
                        VALUES (?,?,?,?,?)""", batch)
                    rows += len(batch)
                    batch = []
            if batch:
                c.executemany("""INSERT OR REPLACE INTO syngenta_inventory 
                    (retailer_id, sku_id, sku_name, sku_qty, week_end_date)
                    VALUES (?,?,?,?,?)""", batch)
                rows += len(batch)
        print(f"[IMPORT] Inventory: {rows}")
    
    # ═══════════════════════════════════════
    # TABLE: POS (sales transactions)
    # ═══════════════════════════════════════
    c.execute("""
    CREATE TABLE IF NOT EXISTS syngenta_pos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        retailer_id TEXT,
        transaction_id TEXT,
        sku_id TEXT,
        sku_name TEXT,
        sku_qty INTEGER,
        sku_price REAL,
        transaction_date TEXT,
        UNIQUE(transaction_id, sku_id)
    )""")
    
    path = os.path.join(SYNGENTA_DIR, 'retailer_pos.csv')
    if os.path.exists(path):
        with open(path, encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f)
            rows = 0
            batch = []
            for r in reader:
                batch.append((r['retailer_id'], r['transaction_id'], r['sku_id'],
                              r['sku_name'], int(r['sku_qty']), float(r['sku_price']),
                              r['transaction_date']))
                if len(batch) >= 5000:
                    c.executemany("""INSERT OR REPLACE INTO syngenta_pos 
                        (retailer_id, transaction_id, sku_id, sku_name, sku_qty, sku_price, transaction_date)
                        VALUES (?,?,?,?,?,?,?)""", batch)
                    rows += len(batch)
                    batch = []
            if batch:
                c.executemany("""INSERT OR REPLACE INTO syngenta_pos 
                    (retailer_id, transaction_id, sku_id, sku_name, sku_qty, sku_price, transaction_date)
                    VALUES (?,?,?,?,?,?,?)""", batch)
                rows += len(batch)
        print(f"[IMPORT] POS: {rows}")
    
    # ═══════════════════════════════════════
    # TABLE: Growers
    # ═══════════════════════════════════════
    c.execute("""
    CREATE TABLE IF NOT EXISTS syngenta_growers (
        grower_id TEXT PRIMARY KEY,
        state TEXT,
        district TEXT,
        tehsil TEXT,
        language TEXT,
        device_type TEXT,
        grower_age INTEGER,
        farm_size REAL
    )""")
    
    path = os.path.join(SYNGENTA_DIR, 'growers.csv')
    if os.path.exists(path):
        with open(path, encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f)
            rows = 0
            for r in reader:
                c.execute("""INSERT OR REPLACE INTO syngenta_growers 
                    (grower_id, state, district, tehsil, language, device_type, grower_age, farm_size)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (r['grower_id'], r['state'], r['district'], r['tehsil'],
                     r['language'], r['device_type'],
                     int(r.get('grower_age', 0) or 0),
                     float(r.get('grower_farm_size', 0) or 0)))
                rows += 1
        print(f"[IMPORT] Growers: {rows}")
    
    # Indexes
    c.execute("CREATE INDEX IF NOT EXISTS idx_inv_retailer ON syngenta_inventory(retailer_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_inv_sku ON syngenta_inventory(sku_name)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_inv_week ON syngenta_inventory(week_end_date)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_pos_retailer ON syngenta_pos(retailer_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_pos_sku ON syngenta_pos(sku_name)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ret_district ON syngenta_retailers(state, district)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_grower_district ON syngenta_growers(state, district)")
    
    conn.commit()
    conn.close()
    print("[IMPORT] Syngenta import complete!")


if __name__ == '__main__':
    import time
    t = time.time()
    import_syngenta()
    print(f"Done in {time.time()-t:.1f}s")
