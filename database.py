# KrishiConnect AI - Database Schema & Initialization
import sqlite3
import os
from config import Config

def get_db():
    """Get database connection with row factory"""
    conn = sqlite3.connect(Config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        # Some non-interactive Windows launches cannot create/open WAL sidecar files.
        # The app can still run safely for local demos with SQLite's default journal mode.
        pass
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    """Create all tables"""
    conn = get_db()
    cursor = conn.cursor()
    
    # ═══════════════════════════════════════
    # TABLE 1: Districts (690 districts)
    # ═══════════════════════════════════════
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS districts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        state TEXT NOT NULL,
        district TEXT NOT NULL,
        language TEXT NOT NULL,
        latitude REAL NOT NULL,
        longitude REAL NOT NULL,
        region_type TEXT DEFAULT 'inland',
        is_active INTEGER DEFAULT 1,
        UNIQUE(state, district)
    )""")
    
    # ═══════════════════════════════════════
    # TABLE 2: District-Crop mapping
    # ═══════════════════════════════════════
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS district_crops (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        district_id INTEGER NOT NULL,
        crop TEXT NOT NULL,
        season TEXT NOT NULL,
        FOREIGN KEY(district_id) REFERENCES districts(id),
        UNIQUE(district_id, crop, season)
    )""")
    
    # ═══════════════════════════════════════
    # TABLE 3: Disease-Product mapping (26 diseases)
    # ═══════════════════════════════════════
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS disease_product_map (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        disease_name TEXT NOT NULL UNIQUE,
        affected_crops TEXT,
        disease_type TEXT,
        product_category TEXT,
        primary_product TEXT NOT NULL,
        primary_chemical TEXT,
        primary_dosage TEXT,
        primary_application TEXT,
        secondary_product TEXT,
        secondary_chemical TEXT,
        secondary_dosage TEXT,
        is_viral INTEGER DEFAULT 0,
        vector_insect TEXT,
        notes TEXT
    )""")
    
    # ═══════════════════════════════════════
    # TABLE 4: Weather cache
    # ═══════════════════════════════════════
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS weather_cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        district_id INTEGER NOT NULL,
        forecast_json TEXT NOT NULL,
        avg_temp REAL,
        avg_humidity REAL,
        total_rainfall REAL,
        max_temp REAL,
        min_temp REAL,
        rainy_days INTEGER,
        fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(district_id) REFERENCES districts(id)
    )""")
    
    # ═══════════════════════════════════════
    # TABLE 5: Disease predictions
    # ═══════════════════════════════════════
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id TEXT,
        district_id INTEGER NOT NULL,
        crop TEXT NOT NULL,
        disease TEXT NOT NULL,
        probability REAL,
        risk_level TEXT,
        weather_summary TEXT,
        matched_conditions TEXT,
        prediction_method TEXT DEFAULT 'ml',
        product_recommended TEXT,
        product_dosage TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(district_id) REFERENCES districts(id)
    )""")
    
    # ═══════════════════════════════════════
    # TABLE 6: Campaigns
    # ═══════════════════════════════════════
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS campaigns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id TEXT,
        prediction_id INTEGER,
        district_id INTEGER NOT NULL,
        campaign_type TEXT DEFAULT 'disease_alert',
        status TEXT DEFAULT 'pending',
        risk_level TEXT,
        crop TEXT,
        disease TEXT,
        product TEXT,
        language TEXT,
        message_whatsapp TEXT,
        message_sms TEXT,
        message_voice_script TEXT,
        voice_file_path TEXT,
        poster_headline TEXT,
        poster_body TEXT,
        poster_file_path TEXT,
        voice_url TEXT,
        poster_url TEXT,
        ab_test_variant TEXT,
        approved_at DATETIME,
        approved_by TEXT DEFAULT 'auto',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(prediction_id) REFERENCES predictions(id),
        FOREIGN KEY(district_id) REFERENCES districts(id)
    )""")
    
    # ═══════════════════════════════════════
    # TABLE 7: Delivery log
    # ═══════════════════════════════════════
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS delivery_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_id INTEGER NOT NULL,
        channel TEXT NOT NULL,
        recipient_phone TEXT,
        delivery_day INTEGER DEFAULT 1,
        scheduled_at DATETIME,
        sent_at DATETIME,
        status TEXT DEFAULT 'pending',
        error_message TEXT,
        twilio_sid TEXT,
        FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
    )""")
    
    # ═══════════════════════════════════════
    # TABLE 8: Farmer feedback
    # ═══════════════════════════════════════
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS farmer_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_id INTEGER,
        district TEXT,
        state TEXT,
        crop TEXT,
        disease TEXT,
        product TEXT,
        farmer_phone_hash TEXT,
        feedback_code INTEGER,
        feedback_type TEXT,
        feedback_text TEXT,
        priority TEXT DEFAULT 'low',
        score INTEGER DEFAULT 0,
        status TEXT DEFAULT 'new',
        assigned_to TEXT,
        resolved_at DATETIME,
        received_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
    )""")
    
    # ═══════════════════════════════════════
    # TABLE 9: A/B Tests
    # ═══════════════════════════════════════
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ab_tests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_id INTEGER,
        variant_a_message TEXT,
        variant_b_message TEXT,
        group_a_count INTEGER DEFAULT 0,
        group_b_count INTEGER DEFAULT 0,
        variant_a_responses INTEGER DEFAULT 0,
        variant_b_responses INTEGER DEFAULT 0,
        winner TEXT,
        status TEXT DEFAULT 'running',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        evaluated_at DATETIME,
        FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
    )""")
    
    # ═══════════════════════════════════════
    # TABLE 10: NDVI alerts
    # ═══════════════════════════════════════
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ndvi_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        latitude REAL,
        longitude REAL,
        ndvi_value REAL,
        stress_level TEXT,
        matched_district_id INTEGER,
        matched_distance_km REAL,
        has_active_crop INTEGER DEFAULT 0,
        alert_type TEXT DEFAULT 'vegetation_stress',
        status TEXT DEFAULT 'pending',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(matched_district_id) REFERENCES districts(id)
    )""")
    
    # ═══════════════════════════════════════
    # TABLE 11: System settings
    # ═══════════════════════════════════════
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    
    # Default settings
    defaults = [
        ('auto_mode', 'true'),
        ('auto_approve_threshold', '0.7'),
        ('active_season', 'kharif'),
        ('pipeline_schedule', '06:00'),
        ('sms_cooldown_hours', '24'),
        ('whatsapp_cooldown_hours', '48'),
        ('voice_cooldown_hours', '168'),
    ]
    for key, value in defaults:
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )
    
    # ═══════════════════════════════════════
    # INDEXES for performance
    # ═══════════════════════════════════════
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_district_crops_season ON district_crops(season)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_predictions_batch ON predictions(batch_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_predictions_district ON predictions(district_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_campaigns_district ON campaigns(district_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_delivery_status ON delivery_log(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_weather_district ON weather_cache(district_id)")
    
    # ═══════════════════════════════════════
    # TABLE 12: Users (Auth + Access Control)
    # ═══════════════════════════════════════
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT DEFAULT 'employee',
        status TEXT DEFAULT 'pending',
        department TEXT,
        approved_by TEXT,
        approved_at DATETIME,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    
    # Seed admin user (ram@gmail.com / 123)
    import hashlib
    admin_hash = hashlib.sha256('123'.encode()).hexdigest()
    cursor.execute("""
        INSERT OR IGNORE INTO users (name, email, password_hash, role, status)
        VALUES (?, ?, ?, 'admin', 'approved')
    """, ('Admin', 'ram@gmail.com', admin_hash))
    
    conn.commit()
    conn.close()
    print(f"Database initialized at: {Config.DATABASE_PATH}")
    print("12 tables + 7 indexes created. Admin seeded.")

def migrate_db():
    """Add new columns to existing tables (safe to run multiple times)"""
    conn = get_db()
    cursor = conn.cursor()
    
    migrations = [
        # delivery_log: track recipient phone for two-way interaction
        "ALTER TABLE delivery_log ADD COLUMN recipient_phone TEXT",
        # farmer_feedback: lead intelligence columns
        "ALTER TABLE farmer_feedback ADD COLUMN state TEXT",
        "ALTER TABLE farmer_feedback ADD COLUMN crop TEXT",
        "ALTER TABLE farmer_feedback ADD COLUMN disease TEXT",
        "ALTER TABLE farmer_feedback ADD COLUMN product TEXT",
        "ALTER TABLE farmer_feedback ADD COLUMN feedback_type TEXT",
        "ALTER TABLE farmer_feedback ADD COLUMN priority TEXT DEFAULT 'low'",
        "ALTER TABLE farmer_feedback ADD COLUMN score INTEGER DEFAULT 0",
        "ALTER TABLE farmer_feedback ADD COLUMN status TEXT DEFAULT 'new'",
        "ALTER TABLE farmer_feedback ADD COLUMN assigned_to TEXT",
        "ALTER TABLE farmer_feedback ADD COLUMN resolved_at DATETIME",
    ]
    
    applied = 0
    for sql in migrations:
        try:
            cursor.execute(sql)
            applied += 1
        except Exception:
            pass  # Column already exists — safe to skip
    
    # Add index for faster feedback lookups
    try:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_feedback_type ON farmer_feedback(feedback_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_feedback_status ON farmer_feedback(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_feedback_priority ON farmer_feedback(priority)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_delivery_phone ON delivery_log(recipient_phone)")
    except Exception:
        pass
    
    conn.commit()
    conn.close()
    if applied > 0:
        print(f"[MIGRATE] {applied} new columns added.")
    else:
        print("[MIGRATE] Schema up to date.")

if __name__ == '__main__':
    init_db()
    migrate_db()
