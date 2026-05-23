# KrishiConnect AI - Main Flask Application
from flask import Flask, jsonify, request
from flask_cors import CORS
from config import Config
from database import get_db, init_db
import os

app = Flask(__name__)
CORS(app)

# Register Blueprints
from routes.weather_routes import weather_bp
from routes.disease_routes import disease_bp
from routes.campaign_routes import campaign_bp
from routes.ndvi_routes import ndvi_bp
from routes.delivery_routes import delivery_bp
from routes.auth_routes import auth_bp
from routes.overview_routes import overview_bp
from routes.inventory_routes import inventory_bp
from routes.webhook_routes import webhook_bp
app.register_blueprint(weather_bp)
app.register_blueprint(disease_bp)
app.register_blueprint(campaign_bp)
app.register_blueprint(ndvi_bp)
app.register_blueprint(delivery_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(overview_bp)
app.register_blueprint(inventory_bp)
app.register_blueprint(webhook_bp)


def prepare_database():
    """Ensure Gunicorn/Render imports start with a usable SQLite schema."""
    try:
        from setup_database import main as setup_database
        setup_database()
    except Exception as exc:
        print(f"[DB] Seed setup skipped: {exc}")
        if not os.path.exists(Config.DATABASE_PATH):
            init_db()
        else:
            try:
                from database import migrate_db
                migrate_db()
            except Exception as migrate_exc:
                print(f"[DB] Migration skipped: {migrate_exc}")


prepare_database()

# ═══════════════════════════════════════
# HEALTH CHECK
# ═══════════════════════════════════════
@app.route('/api/health', methods=['GET'])
def health_check():
    conn = get_db()
    districts = conn.execute("SELECT COUNT(*) FROM districts").fetchone()[0]
    diseases = conn.execute("SELECT COUNT(*) FROM disease_product_map").fetchone()[0]
    crops = conn.execute("SELECT COUNT(*) FROM district_crops").fetchone()[0]
    conn.close()
    return jsonify({
        'status': 'healthy',
        'database': {
            'districts': districts,
            'disease_mappings': diseases,
            'crop_entries': crops
        }
    })

# ═══════════════════════════════════════
# DISTRICT ROUTES
# ═══════════════════════════════════════
@app.route('/api/districts', methods=['GET'])
def get_districts():
    """Get all districts, optionally filter by state/season/crop"""
    state = request.args.get('state')
    season = request.args.get('season')
    crop = request.args.get('crop')
    
    conn = get_db()
    query = """
        SELECT DISTINCT d.id, d.state, d.district, d.language, 
               d.latitude, d.longitude
        FROM districts d
        JOIN district_crops dc ON d.id = dc.district_id
        WHERE 1=1
    """
    params = []
    
    if state:
        query += " AND d.state = ?"
        params.append(state)
    if season:
        query += " AND dc.season = ?"
        params.append(season)
    if crop:
        query += " AND dc.crop = ?"
        params.append(crop)
    
    query += " ORDER BY d.state, d.district"
    
    rows = conn.execute(query, params).fetchall()
    conn.close()
    
    return jsonify({
        'count': len(rows),
        'districts': [dict(r) for r in rows]
    })

@app.route('/api/districts/<int:district_id>/crops', methods=['GET'])
def get_district_crops(district_id):
    """Get all crops for a specific district"""
    conn = get_db()
    
    district = conn.execute(
        "SELECT * FROM districts WHERE id = ?", (district_id,)
    ).fetchone()
    
    if not district:
        conn.close()
        return jsonify({'error': 'District not found'}), 404
    
    crops = conn.execute(
        "SELECT crop, season FROM district_crops WHERE district_id = ? ORDER BY season, crop",
        (district_id,)
    ).fetchall()
    
    conn.close()
    
    return jsonify({
        'district': dict(district),
        'crops': [dict(c) for c in crops]
    })

@app.route('/api/states', methods=['GET'])
def get_states():
    """Get all unique states with district count"""
    conn = get_db()
    rows = conn.execute("""
        SELECT state, COUNT(*) as district_count, 
               GROUP_CONCAT(DISTINCT language) as languages
        FROM districts 
        GROUP BY state 
        ORDER BY state
    """).fetchall()
    conn.close()
    
    return jsonify({
        'count': len(rows),
        'states': [dict(r) for r in rows]
    })

@app.route('/api/crops', methods=['GET'])
def get_crops():
    """Get all unique crops with count"""
    season = request.args.get('season')
    conn = get_db()
    
    query = "SELECT crop, season, COUNT(*) as district_count FROM district_crops"
    params = []
    if season:
        query += " WHERE season = ?"
        params.append(season)
    query += " GROUP BY crop, season ORDER BY crop"
    
    rows = conn.execute(query, params).fetchall()
    conn.close()
    
    return jsonify({'crops': [dict(r) for r in rows]})

# ═══════════════════════════════════════
# DISEASE-PRODUCT ROUTES
# ═══════════════════════════════════════
@app.route('/api/diseases', methods=['GET'])
def get_diseases():
    """Get all disease-product mappings"""
    conn = get_db()
    rows = conn.execute("SELECT * FROM disease_product_map ORDER BY disease_name").fetchall()
    conn.close()
    return jsonify({
        'count': len(rows),
        'diseases': [dict(r) for r in rows]
    })

@app.route('/api/diseases/<disease_name>/product', methods=['GET'])
def get_product_for_disease(disease_name):
    """Get Syngenta product recommendation for a disease"""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM disease_product_map WHERE disease_name = ?",
        (disease_name,)
    ).fetchone()
    conn.close()
    
    if not row:
        return jsonify({'error': f'Disease {disease_name} not found'}), 404
    
    return jsonify(dict(row))

# ═══════════════════════════════════════
# SETTINGS ROUTES
# ═══════════════════════════════════════
@app.route('/api/settings', methods=['GET'])
def get_settings():
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return jsonify({k: v for k, v in rows})

@app.route('/api/settings', methods=['POST'])
def update_settings():
    data = request.json
    conn = get_db()
    for key, value in data.items():
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (key, str(value))
        )
    conn.commit()
    conn.close()
    return jsonify({'status': 'updated', 'settings': data})

# ═══════════════════════════════════════
# DASHBOARD STATS
# ═══════════════════════════════════════
@app.route('/api/dashboard/stats', methods=['GET'])
def dashboard_stats():
    conn = get_db()
    
    # ── Core Counts ──
    stats = {
        'districts': conn.execute("SELECT COUNT(*) FROM districts").fetchone()[0],
        'states': conn.execute("SELECT COUNT(DISTINCT state) FROM districts").fetchone()[0],
        'campaigns_total': conn.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0],
        'campaigns_pending': conn.execute("SELECT COUNT(*) FROM campaigns WHERE status='pending'").fetchone()[0],
        'campaigns_sent': conn.execute("SELECT COUNT(*) FROM campaigns WHERE status='completed'").fetchone()[0],
        'feedback_received': conn.execute("SELECT COUNT(*) FROM farmer_feedback").fetchone()[0],
        'high_value_leads': conn.execute("SELECT COUNT(*) FROM farmer_feedback WHERE score >= 8").fetchone()[0],
        'buy_intent_leads': conn.execute("SELECT COUNT(*) FROM farmer_feedback WHERE feedback_type='buy_intent'").fetchone()[0],
        'field_issues': conn.execute("SELECT COUNT(*) FROM farmer_feedback WHERE feedback_type='field_issue'").fetchone()[0],
    }
    
    # Engagement rate
    campaigns_sent = stats['campaigns_sent'] or 1
    stats['engagement_rate'] = round((stats['feedback_received'] / campaigns_sent) * 100, 1)
    
    # ── Syngenta Network ──
    try:
        stats['farmer_count'] = conn.execute("SELECT COUNT(*) FROM syngenta_growers").fetchone()[0]
        stats['retailer_count'] = conn.execute("SELECT COUNT(*) FROM syngenta_retailers").fetchone()[0]
        
        # Smartphone penetration (lowercase 'smartphone' in data)
        smart = conn.execute("SELECT COUNT(*) FROM syngenta_growers WHERE device_type='smartphone'").fetchone()[0]
        total_g = stats['farmer_count'] or 1
        stats['smartphone_pct'] = round((smart / total_g) * 100)
        
        # Average farm size (column is farm_size, not farm_size_acres)
        avg_farm = conn.execute("SELECT AVG(farm_size) FROM syngenta_growers").fetchone()[0]
        stats['avg_farm_size'] = round(avg_farm, 1) if avg_farm else 0
        
        # Total acreage monitored
        total_acres = conn.execute("SELECT SUM(farm_size) FROM syngenta_growers").fetchone()[0]
        stats['total_acreage'] = round(total_acres) if total_acres else 0
    except:
        stats['farmer_count'] = 0
        stats['retailer_count'] = 0
        stats['smartphone_pct'] = 0
        stats['avg_farm_size'] = 0
        stats['total_acreage'] = 0
    
    # ── Agricultural Intelligence ──
    # campaigns table has district_id (FK to districts.id)
    # districts table has: id, state, district, language
    try:
        # Top at-risk crop (most HIGH/MEDIUM campaigns)
        row = conn.execute("""
            SELECT crop, COUNT(*) as cnt FROM campaigns 
            WHERE risk_level IN ('HIGH','MODERATE','MEDIUM')
            GROUP BY crop ORDER BY cnt DESC LIMIT 1
        """).fetchone()
        if row:
            risk_districts = conn.execute("""
                SELECT COUNT(DISTINCT district_id) FROM campaigns 
                WHERE crop=? AND risk_level IN ('HIGH','MODERATE','MEDIUM')
            """, (row[0],)).fetchone()[0]
            stats['top_risk_crop'] = {'crop': row[0], 'count': row[1], 'districts': risk_districts}
        else:
            stats['top_risk_crop'] = None
        
        # Top disease threat
        row = conn.execute("""
            SELECT disease, COUNT(*) as cnt FROM campaigns 
            WHERE risk_level IN ('HIGH','MODERATE','MEDIUM')
            GROUP BY disease ORDER BY cnt DESC LIMIT 1
        """).fetchone()
        if row:
            stats['top_disease'] = {'disease': row[0], 'count': row[1]}
        else:
            stats['top_disease'] = None
        
        # Most demanded Syngenta product
        row = conn.execute("""
            SELECT product, COUNT(*) as cnt FROM campaigns
            WHERE product IS NOT NULL AND product != ''
            GROUP BY product ORDER BY cnt DESC LIMIT 1
        """).fetchone()
        if row:
            stats['top_product'] = {'product': row[0], 'count': row[1]}
        else:
            stats['top_product'] = None
        
        # Highest risk state (JOIN with districts for state)
        row = conn.execute("""
            SELECT d.state, COUNT(*) as cnt FROM campaigns c
            JOIN districts d ON c.district_id = d.id
            WHERE c.risk_level IN ('HIGH','MODERATE','MEDIUM')
            GROUP BY d.state ORDER BY cnt DESC LIMIT 1
        """).fetchone()
        if row:
            stats['hottest_state'] = {'state': row[0], 'count': row[1]}
        else:
            stats['hottest_state'] = None
        
        # Top 5 at-risk states
        rows = conn.execute("""
            SELECT d.state, COUNT(*) as cnt FROM campaigns c
            JOIN districts d ON c.district_id = d.id
            WHERE c.risk_level IN ('HIGH','MODERATE','MEDIUM')
            GROUP BY d.state ORDER BY cnt DESC LIMIT 5
        """).fetchall()
        stats['top_risk_states'] = [{'state': r[0], 'count': r[1]} for r in rows]
        
        # Top 5 diseases across all campaigns
        rows = conn.execute("""
            SELECT disease, COUNT(*) as cnt FROM campaigns
            WHERE disease IS NOT NULL AND disease != ''
            GROUP BY disease ORDER BY cnt DESC LIMIT 5
        """).fetchall()
        stats['top_diseases'] = [{'disease': r[0], 'count': r[1]} for r in rows]
        
        # Products distribution
        rows = conn.execute("""
            SELECT product, COUNT(*) as cnt FROM campaigns
            WHERE product IS NOT NULL AND product != ''
            GROUP BY product ORDER BY cnt DESC LIMIT 5
        """).fetchall()
        stats['top_products'] = [{'product': r[0], 'count': r[1]} for r in rows]
        
    except Exception as e:
        print(f"[STATS] Intelligence query error: {e}")
        stats['top_risk_crop'] = None
        stats['top_disease'] = None
        stats['top_product'] = None
        stats['hottest_state'] = None
        stats['top_risk_states'] = []
        stats['top_diseases'] = []
        stats['top_products'] = []
    
    conn.close()
    return jsonify(stats)


# ═══════════════════════════════════════
# RUN
# ═══════════════════════════════════════
@app.route('/health')
def health():
    return 'OK', 200

if __name__ == '__main__':
    # Initialize DB if not exists
    if not os.path.exists(Config.DATABASE_PATH):
        init_db()
        print("Database initialized. Run import_data.py to import Excel data.")
    
    # Run migrations (adds new columns safely)
    from database import migrate_db
    migrate_db()
    
    print("\n[KrishiConnect AI] Server Starting...")
    print(f"[DB] {Config.DATABASE_PATH}")
    print(f"[API] http://localhost:5000/api/health")
    print(f"[WEBHOOK] http://localhost:5000/api/webhook/twilio")
    print(f"[LEADS] http://localhost:5000/api/leads")
    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', debug=os.environ.get('FLASK_DEBUG') == '1', port=port, threaded=True)
