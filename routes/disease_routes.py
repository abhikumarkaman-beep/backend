# KrishiConnect AI - Disease Prediction Routes
from flask import Blueprint, jsonify, request
from services.disease_service import DiseasePredictor
from services.weather_service import WeatherService
from database import get_db
import json
import uuid
from datetime import datetime

disease_bp = Blueprint('disease', __name__)
predictor = DiseasePredictor()
weather_service = WeatherService()


@disease_bp.route('/api/predict/<int:district_id>', methods=['GET'])
def predict_district(district_id):
    """Predict diseases for a single district (fetches weather + predicts)"""
    conn = get_db()
    district = conn.execute(
        "SELECT * FROM districts WHERE id = ?", (district_id,)
    ).fetchone()
    conn.close()
    
    if not district:
        return jsonify({'error': 'District not found'}), 404
    
    # Fetch weather
    weather_result = weather_service.fetch_and_cache(
        district['id'], district['latitude'], district['longitude']
    )
    
    if not weather_result.get('summary'):
        return jsonify({'error': 'Weather fetch failed'}), 500
    
    # Predict diseases
    predictions = predictor.predict_for_district(
        district_id, weather_result['summary']
    )
    
    return jsonify({
        'district': dict(district),
        'weather': weather_result['summary'],
        'predictions': predictions,
        'count': len(predictions)
    })


@disease_bp.route('/api/predict/batch', methods=['POST'])
def predict_batch():
    """Batch predict: fetch weather + predict for all districts in a season"""
    data = request.json or {}
    season = data.get('season', predictor._get_current_season())
    state_filter = data.get('state')
    limit = data.get('limit', 50)
    
    conn = get_db()
    
    query = """
        SELECT DISTINCT d.id, d.latitude, d.longitude
        FROM districts d
        JOIN district_crops dc ON d.id = dc.district_id
        WHERE dc.season = ?
    """
    params = [season]
    
    if state_filter:
        query += " AND d.state = ?"
        params.append(state_filter)
    
    query += f" LIMIT {int(limit)}"
    
    districts = conn.execute(query, params).fetchall()
    conn.close()
    
    batch_id = str(uuid.uuid4())[:8]
    
    # Step 1: Fetch weather for all districts
    weather_data = []
    for d in districts:
        w = weather_service.fetch_and_cache(d['id'], d['latitude'], d['longitude'])
        if w.get('summary'):
            weather_data.append({
                'district_id': d['id'],
                'weather_summary': w['summary']
            })
    
    # Step 2: Batch predict
    predictions = predictor.predict_batch(weather_data, season)
    
    # Step 3: Save predictions to database
    conn = get_db()
    saved = 0
    for pred in predictions:
        conn.execute("""
            INSERT INTO predictions 
            (batch_id, district_id, crop, disease, probability, risk_level,
             weather_summary, prediction_method, product_recommended, product_dosage)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            batch_id, pred['district_id'], pred['crop'], pred['disease'],
            pred['probability'], pred['risk_level'],
            json.dumps(pred['weather_summary']), pred['method'],
            pred['product'], pred['dosage']
        ))
        saved += 1
    conn.commit()
    conn.close()
    
    # Stats
    high_risk = [p for p in predictions if p['risk_level'] == 'HIGH']
    
    return jsonify({
        'batch_id': batch_id,
        'season': season,
        'districts_processed': len(weather_data),
        'total_predictions': len(predictions),
        'high_risk': len(high_risk),
        'saved_to_db': saved,
        'top_risks': predictions[:10]
    })


@disease_bp.route('/api/predict/test', methods=['GET'])
def test_predict():
    """Quick test - predict for district_id=1"""
    conn = get_db()
    district = conn.execute("SELECT * FROM districts LIMIT 1").fetchone()
    conn.close()
    
    if not district:
        return jsonify({'error': 'No districts in DB'}), 500
    
    weather = weather_service.fetch_and_cache(
        district['id'], district['latitude'], district['longitude']
    )
    
    predictions = predictor.predict_for_district(
        district['id'], weather.get('summary', {})
    )
    
    return jsonify({
        'district': dict(district),
        'weather': weather.get('summary', {}),
        'predictions': predictions,
        'model_loaded': predictor.model_loaded
    })
