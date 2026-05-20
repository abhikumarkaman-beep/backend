# KrishiConnect AI - Weather API Routes
from flask import Blueprint, jsonify, request
from services.weather_service import WeatherService
from database import get_db

weather_bp = Blueprint('weather', __name__)
weather_service = WeatherService()


@weather_bp.route('/api/weather/<int:district_id>', methods=['GET'])
def get_weather(district_id):
    """Get 7-day weather forecast for a district"""
    conn = get_db()
    district = conn.execute(
        "SELECT * FROM districts WHERE id = ?", (district_id,)
    ).fetchone()
    conn.close()
    
    if not district:
        return jsonify({'error': 'District not found'}), 404
    
    result = weather_service.fetch_and_cache(
        district['id'], district['latitude'], district['longitude']
    )
    
    # Check for weather alerts
    alerts = []
    if result.get('summary'):
        alerts = weather_service.should_send_weather_alert(result['summary'])
    
    return jsonify({
        'district': dict(district),
        'weather': result,
        'alerts': alerts
    })


@weather_bp.route('/api/weather/batch', methods=['POST'])
def batch_weather():
    """Fetch weather for all districts in a season"""
    data = request.json or {}
    season = data.get('season', 'Kharif')
    
    results = weather_service.batch_fetch(season=season, max_workers=10)
    
    return jsonify({
        'season': season,
        'total_districts': results['total'],
        'fetched': results['success'],
        'cached': results['cached'],
        'failed': results['failed'],
    })


@weather_bp.route('/api/weather/test', methods=['GET'])
def test_weather():
    """Quick test - fetch weather for Delhi"""
    result = weather_service.fetch_forecast(28.6139, 77.2090)
    return jsonify({
        'location': 'New Delhi (test)',
        'result': result
    })
