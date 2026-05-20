# KrishiConnect AI - Delivery Routes
from flask import Blueprint, jsonify, request
from services.delivery_service import DeliveryService

delivery_bp = Blueprint('delivery', __name__)
delivery_service = DeliveryService()


@delivery_bp.route('/api/delivery/test', methods=['POST'])
def test_whatsapp():
    """Send a test WhatsApp message to a single number"""
    data = request.json or {}
    number = data.get('number', '')
    message = data.get('message', 'Namaste! KrishiConnect AI test message. Sab kaam kar raha hai!')
    
    if not number:
        return jsonify({'error': 'number required'}), 400
    
    result = delivery_service.send_test_message(number, message)
    return jsonify(result)


@delivery_bp.route('/api/delivery/campaign/<int:campaign_id>', methods=['POST'])
def send_campaign(campaign_id):
    """Send a campaign to all test numbers"""
    result = delivery_service.send_campaign_to_test_numbers(campaign_id)
    return jsonify(result)


@delivery_bp.route('/api/delivery/broadcast', methods=['POST'])
def broadcast():
    """Send custom message to all test numbers"""
    data = request.json or {}
    message = data.get('message', '')
    
    if not message:
        return jsonify({'error': 'message required'}), 400
    
    results = []
    for number in delivery_service.test_numbers:
        number = number.strip()
        if not number:
            continue
        r = delivery_service.send_whatsapp(number, message)
        results.append({'number': number, 'result': r})
    
    return jsonify({
        'sent_to': len(results),
        'results': results
    })


@delivery_bp.route('/api/delivery/stats', methods=['GET'])
def delivery_stats():
    """Get delivery statistics"""
    stats = delivery_service.get_delivery_stats()
    return jsonify(stats)


@delivery_bp.route('/api/delivery/sandbox-info', methods=['GET'])
def sandbox_info():
    """Return active account's sandbox code for frontend"""
    info = delivery_service.get_active_info()
    return jsonify(info)


@delivery_bp.route('/api/delivery/switch-account', methods=['POST'])
def switch_account():
    """Manually switch to next Twilio account"""
    result = delivery_service.switch_account()
    return jsonify(result)

