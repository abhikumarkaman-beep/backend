# KrishiConnect AI - Crop Overview Routes (Persistent pipeline health data)
from flask import Blueprint, jsonify, request
from database import get_db
import json

overview_bp = Blueprint('overview', __name__)


@overview_bp.route('/api/overview/health', methods=['GET'])
def crop_health_overview():
    """
    Get persistent pipeline health data — state-wise grouped.
    Reads from predictions + districts tables (DB-persisted).
    Optional filters: state, syngenta_only
    """
    state_filter = request.args.get('state')
    syngenta_only = request.args.get('syngenta_only', 'false').lower() == 'true'
    
    conn = get_db()
    
    pred_count = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
    if pred_count == 0 and not syngenta_only:
        conn.close()
        return jsonify({
            'districts': [],
            'stats': {'total': 0, 'healthy': 0, 'medium': 0, 'high': 0},
            'last_run': None,
        })
    
    latest_pred = """
        SELECT p1.* FROM predictions p1
        INNER JOIN (
            SELECT district_id, MAX(id) as max_id
            FROM predictions
            GROUP BY district_id
        ) p2 ON p1.district_id = p2.district_id AND p1.id = p2.max_id
    """
    campaign_join = """
        LEFT JOIN (
            SELECT prediction_id, MIN(id) as id, MIN(status) as status
            FROM campaigns
            GROUP BY prediction_id
        ) c_map ON p.id = c_map.prediction_id
        LEFT JOIN campaigns c ON c.id = c_map.id
    """
    
    if syngenta_only:
        query = f"""
            SELECT d.id as district_id, d.state, d.district, d.language,
                   d.latitude, d.longitude,
                   p.id as prediction_id, p.batch_id, p.crop, p.disease, 
                   p.probability, p.risk_level, p.product_recommended,
                   p.prediction_method, p.weather_summary, p.created_at,
                   c.id as campaign_id, c.status as campaign_status
            FROM districts d
            INNER JOIN (
                SELECT DISTINCT LOWER(TRIM(district)) AS district, LOWER(TRIM(state)) AS state
                FROM syngenta_growers
            ) sg ON LOWER(TRIM(d.district)) = sg.district AND LOWER(TRIM(d.state)) = sg.state
            LEFT JOIN ({latest_pred}) p ON d.id = p.district_id
            {campaign_join}
            WHERE d.is_active = 1
        """
    else:
        query = f"""
            SELECT d.id as district_id, d.state, d.district, d.language,
                   d.latitude, d.longitude,
                   p.id as prediction_id, p.batch_id, p.crop, p.disease, 
                   p.probability, p.risk_level, p.product_recommended,
                   p.prediction_method, p.weather_summary, p.created_at,
                   c.id as campaign_id, c.status as campaign_status
            FROM districts d
            INNER JOIN ({latest_pred}) p ON d.id = p.district_id
            {campaign_join}
            WHERE d.is_active = 1
        """
    params = []
    if state_filter:
        query += " AND d.state = ?"
        params.append(state_filter)
    
    query += " ORDER BY d.state, d.district"
    rows = conn.execute(query, params).fetchall()
    
    # Get the latest batch timestamp
    latest = conn.execute(
        "SELECT MAX(created_at) as last_run FROM predictions"
    ).fetchone()
    last_run = latest['last_run'] if latest else None
    
    conn.close()
    # Build response — all districts with a stored prediction (full pipeline run)
    districts = []
    for r in rows:
        row = dict(r)
        if not row.get('prediction_id'):
            continue
        weather = {}
        try:
            if row['weather_summary']:
                weather = json.loads(row['weather_summary']) if isinstance(row['weather_summary'], str) else row['weather_summary']
        except Exception:
            pass
        
        if row['risk_level'] == 'HEALTHY' or row['disease'] == 'None':
            # Healthy — scanned but no risk
            districts.append({
                'district_id': row['district_id'],
                'district': row['district'],
                'state': row['state'],
                'language': row['language'],
                'lat': row['latitude'],
                'lon': row['longitude'],
                'status': 'healthy',
                'color': 'green',
                'crop': None,
                'disease': None,
                'probability': 0,
                'risk_level': None,
                'product': None,
                'method': row['prediction_method'],
                'campaign_id': None,
                'campaign_status': None,
                'scanned_at': row['created_at'],
                'weather': weather,
            })
        else:
            # At risk
            districts.append({
                'district_id': row['district_id'],
                'district': row['district'],
                'state': row['state'],
                'language': row['language'],
                'lat': row['latitude'],
                'lon': row['longitude'],
                'status': 'at_risk',
                'color': 'red' if row['risk_level'] == 'HIGH' else 'orange' if row['risk_level'] == 'MODERATE' else 'yellow',
                'crop': row['crop'],
                'disease': row['disease'],
                'probability': row['probability'],
                'risk_level': row['risk_level'],
                'product': row['product_recommended'],
                'method': row['prediction_method'],
                'campaign_id': row['campaign_id'],
                'campaign_status': row['campaign_status'],
                'scanned_at': row['created_at'],
                'weather': weather,
            })
    
    # Stats
    total = len(districts)
    healthy = sum(1 for d in districts if d['status'] == 'healthy')
    advisory = sum(1 for d in districts if d.get('risk_level') == 'ADVISORY')
    moderate = sum(1 for d in districts if d.get('risk_level') == 'MODERATE')
    high = sum(1 for d in districts if d.get('risk_level') == 'HIGH')
    
    return jsonify({
        'districts': districts,
        'stats': {
            'total': total,
            'healthy': healthy,
            'advisory': advisory,
            'moderate': moderate,
            'high': high,
        },
        'last_run': last_run,
        'syngenta_only': syngenta_only,
        'expected_syngenta': 33 if syngenta_only else None,
    })
