# KrishiConnect AI - NDVI Routes
from flask import Blueprint, jsonify, request
from services.ndvi_service import NDVIService

ndvi_bp = Blueprint('ndvi', __name__)
ndvi_service = NDVIService()


@ndvi_bp.route('/api/ndvi/scan', methods=['POST'])
def run_scan():
    """Run NDVI satellite scan with 3-tier fallback + 3-case classification"""
    data = request.json or {}
    state = data.get('state')
    num_points = data.get('points', 30)
    result = ndvi_service.scan_area(state=state, num_points=num_points)
    return jsonify(result)


@ndvi_bp.route('/api/ndvi/alerts', methods=['GET'])
def get_alerts():
    """Get NDVI stress alerts with optional state filter"""
    stress = request.args.get('stress_level')
    state = request.args.get('state')
    limit = request.args.get('limit', 200, type=int)
    alerts = ndvi_service.get_alerts(stress_level=stress, state=state, limit=limit)
    return jsonify({'count': len(alerts), 'alerts': alerts})


@ndvi_bp.route('/api/ndvi/heatmap', methods=['GET'])
def get_heatmap():
    """Get NDVI data for map heatmap with optional state filter"""
    state = request.args.get('state')
    data = ndvi_service.get_ndvi_heatmap_data(state=state)
    return jsonify({'count': len(data), 'points': data})
