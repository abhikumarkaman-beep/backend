# KrishiConnect AI - Webhook & Feedback Routes
# Twilio webhook for two-way farmer interaction + Leads dashboard API
from flask import Blueprint, request, jsonify
from services.feedback_service import FeedbackService

webhook_bp = Blueprint('webhook', __name__)
feedback_service = FeedbackService()


# ═══════════════════════════════════════
# TWILIO WEBHOOK — receives farmer replies
# ═══════════════════════════════════════

@webhook_bp.route('/api/webhook/twilio', methods=['POST'])
def twilio_webhook():
    """
    Twilio calls this when a farmer replies on WhatsApp.
    
    Twilio sends POST with form data:
    - From: "whatsapp:+919812345678"
    - Body: "2" (the reply text)
    - MediaUrl0: (optional, if farmer sends image)
    """
    try:
        from_number = request.form.get('From', '')
        body = request.form.get('Body', '').strip()
        media_url = request.form.get('MediaUrl0')
        num_media = request.form.get('NumMedia', '0')
        
        print(f"[WEBHOOK] Incoming from {from_number}: '{body}' | Media: {num_media}")
        
        # Process the reply
        result = feedback_service.process_incoming(from_number, body, media_url)
        
        # Send auto-reply via TwiML response
        try:
            from twilio.twiml.messaging_response import MessagingResponse
            resp = MessagingResponse()
            resp.message(result['auto_reply'])
            return str(resp), 200, {'Content-Type': 'text/xml'}
        except ImportError:
            # If twilio not installed, return plain XML
            reply_text = result['auto_reply']
            twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>{reply_text}</Message>
</Response>"""
            return twiml, 200, {'Content-Type': 'text/xml'}
    
    except Exception as e:
        print(f"[WEBHOOK] Error: {e}")
        # Still return valid TwiML to avoid Twilio retries
        return """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>Dhanyavaad! Aapka sandesh mil gaya. 🙏</Message>
</Response>""", 200, {'Content-Type': 'text/xml'}


# ═══════════════════════════════════════
# SIMULATE WEBHOOK — for demo/testing without Twilio
# ═══════════════════════════════════════

@webhook_bp.route('/api/webhook/simulate', methods=['POST'])
def simulate_webhook():
    """
    Simulate a farmer reply for demo purposes.
    No Twilio needed — directly process feedback.
    
    JSON body: { "phone": "9812345678", "reply": "2" }
    """
    data = request.get_json(silent=True) or {}
    phone = ''.join(ch for ch in str(data.get('phone', '9999999999')) if ch.isdigit())
    if len(phone) > 10 and phone.startswith('91'):
        phone = phone[-10:]
    if not phone:
        phone = '9999999999'
    reply = str(data.get('reply', '1')).strip() or '1'
    
    # Format as Twilio would
    from_number = f"whatsapp:+91{phone}"
    
    result = feedback_service.process_incoming(from_number, reply)
    
    return jsonify({
        'status': 'processed',
        'phone': phone,
        'feedback': result,
    })


# ═══════════════════════════════════════
# LEADS DASHBOARD API
# ═══════════════════════════════════════

@webhook_bp.route('/api/leads', methods=['GET'])
def get_leads():
    """Get all farmer feedback/leads with filters"""
    status = request.args.get('status')
    priority = request.args.get('priority')
    feedback_type = request.args.get('type')
    limit = request.args.get('limit', 50, type=int)
    
    leads = feedback_service.get_leads(status, priority, feedback_type, limit)
    
    return jsonify({
        'count': len(leads),
        'leads': leads,
    })


@webhook_bp.route('/api/leads/<int:feedback_id>/status', methods=['PUT'])
def update_lead(feedback_id):
    """Update lead status: new → assigned → resolved"""
    data = request.json
    status = data.get('status', 'assigned')
    assigned_to = data.get('assigned_to')
    
    result = feedback_service.update_lead_status(feedback_id, status, assigned_to)
    return jsonify(result)


@webhook_bp.route('/api/leads/<int:feedback_id>/retailer', methods=['GET'])
def get_lead_retailer(feedback_id):
    """Get nearest retailers for a buy-intent lead"""
    result = feedback_service.get_retailer_for_lead(feedback_id)
    return jsonify(result)


# ═══════════════════════════════════════
# ENGAGEMENT ANALYTICS API
# ═══════════════════════════════════════

@webhook_bp.route('/api/engagement', methods=['GET'])
def get_engagement():
    """Campaign engagement metrics"""
    stats = feedback_service.get_engagement_stats()
    return jsonify(stats)


@webhook_bp.route('/api/engagement/ground-truth', methods=['GET'])
def get_ground_truth():
    """ML prediction vs farmer field reports validation"""
    result = feedback_service.get_ground_truth_validation()
    return jsonify(result)


@webhook_bp.route('/api/engagement/outbreaks', methods=['GET'])
def get_outbreaks():
    """Detect potential disease outbreaks from farmer reports"""
    threshold = request.args.get('threshold', 3, type=int)
    result = feedback_service.detect_outbreaks(threshold)
    return jsonify(result)
