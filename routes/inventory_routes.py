# KrishiConnect AI - Inventory Routes
from flask import Blueprint, jsonify, request
from services.inventory_service import InventoryService
from config import Config

inventory_bp = Blueprint('inventory', __name__)
inventory_service = InventoryService()


@inventory_bp.route('/api/inventory/overview', methods=['GET'])
def overview():
    """Dashboard overview stats — retailers, stock, growers"""
    stats = inventory_service.get_overview_stats()
    return jsonify(stats)


@inventory_bp.route('/api/inventory/supply-alerts', methods=['GET'])
def supply_alerts():
    """Supply chain alerts — demand vs stock gaps"""
    alerts = inventory_service.get_supply_chain_alerts()
    return jsonify({
        'alerts': alerts,
        'total': len(alerts),
        'urgent': len([a for a in alerts if a['status'] == 'URGENT']),
        'restock': len([a for a in alerts if a['status'] == 'RESTOCK']),
        'ok': len([a for a in alerts if a['status'] == 'OK']),
        'no_coverage': len([a for a in alerts if a['status'] == 'NO_COVERAGE']),
    })


@inventory_bp.route('/api/inventory/channel-routing', methods=['GET'])
def channel_routing():
    """Channel Routing Intelligence — device-type based delivery optimization"""
    data = inventory_service.get_channel_routing()
    return jsonify(data)


@inventory_bp.route('/api/inventory/notify-retailer', methods=['POST'])
def notify_retailer():
    """Send real WhatsApp supply alert to demo retailer number"""
    data = request.json
    district = data.get('district', 'Unknown')
    state = data.get('state', '')
    product = data.get('product', 'N/A')
    deficit = data.get('deficit', 0)
    demand = data.get('demand', 0)
    stock = data.get('stock', 0)
    
    # Build professional message
    message = (
        f"📦 *Syngenta Supply Alert!*\n\n"
        f"District: *{district}*, {state}\n"
        f"Product: *{product}*\n\n"
        f"📊 Predicted Demand: *{demand} units*\n"
        f"📦 Current Stock: *{stock} units*\n"
        f"⚠️ Deficit: *-{deficit} units*\n\n"
        f"High disease risk predicted in this area.\n"
        f"Please replenish stock immediately.\n\n"
        f"_Syngenta AI Supply Intelligence_\n"
        f"Helpline: *1800-123-4567*"
    )
    
    # Try sending real WhatsApp
    demo_number = Config.DEMO_RETAILER_NUMBER
    if demo_number:
        try:
            from services.delivery_service import DeliveryService
            ds = DeliveryService()
            result = ds.send_whatsapp(demo_number, message)
            
            if result.get('status') == 'sent':
                print(f"[NOTIFY] Retailer alert SENT to {demo_number} for {district}")
                return jsonify({
                    'status': 'sent',
                    'message': f'Supply alert sent to retailer!',
                    'sid': result.get('sid'),
                    'district': district,
                    'product': product,
                })
            else:
                print(f"[NOTIFY] Twilio failed: {result.get('error')}")
                return jsonify({
                    'status': 'notified',
                    'message': f'Retailer marked as notified (delivery: {result.get("status")})',
                    'district': district,
                })
        except Exception as e:
            print(f"[NOTIFY] Error: {e}")
            return jsonify({
                'status': 'notified',
                'message': 'Retailer marked as notified (offline mode)',
                'district': district,
            })
    else:
        # No demo number configured — just mark as notified
        return jsonify({
            'status': 'notified',
            'message': 'Retailer marked as notified',
            'district': district,
        })
