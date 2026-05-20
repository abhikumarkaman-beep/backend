# KrishiConnect AI - Campaign API Routes
from flask import Blueprint, jsonify, request, Response
from services.campaign_service import CampaignService
import json

campaign_bp = Blueprint('campaign', __name__)
campaign_service = CampaignService()


@campaign_bp.route('/api/campaign/run', methods=['POST'])
def run_pipeline():
    """
    Run the REAL pipeline: Season → Districts → Weather → ML Predict → Campaigns.
    Options:
      state: filter by state (null = All India)
      limit: max districts to process
    """
    data = request.json or {}
    season = data.get('season')
    state = data.get('state', None)
    limit = data.get('limit', 1000)
    simulate = data.get('simulate_weather', False)
    
    result = campaign_service.run_pipeline(
        season=season, state=state, limit=limit,
        use_simulated_weather=simulate
    )
    return jsonify(result)


@campaign_bp.route('/api/campaign/stream', methods=['GET'])
def stream_pipeline():
    """
    SSE streaming pipeline — yields each district result in real-time.
    Query params: state, limit, syngenta_only
    """
    state = request.args.get('state', None)
    limit = request.args.get('limit', 1000, type=int)
    syngenta_only = request.args.get('syngenta_only', 'false').lower() == 'true'
    
    def generate():
        for event in campaign_service.run_pipeline_stream(
            state=state, limit=limit, syngenta_only=syngenta_only
        ):
            yield f"data: {json.dumps(event)}\n\n"
    
    return Response(
        generate(),
        content_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Access-Control-Allow-Origin': '*',
        }
    )


@campaign_bp.route('/api/campaign/pipeline-status', methods=['GET'])
def pipeline_status():
    """Check if pipeline is currently running"""
    return jsonify({'running': campaign_service.is_pipeline_running()})


@campaign_bp.route('/api/campaign/list', methods=['GET'])
def list_campaigns():
    """List campaigns with filters"""
    status = request.args.get('status')
    state = request.args.get('state')
    batch_id = request.args.get('batch_id')
    limit = request.args.get('limit', 50, type=int)
    
    campaigns = campaign_service.get_campaigns(
        status=status, state=state, batch_id=batch_id, limit=limit
    )
    return jsonify({'count': len(campaigns), 'campaigns': campaigns})


@campaign_bp.route('/api/campaign/approve', methods=['POST'])
def approve_campaigns():
    """Approve campaigns and optionally send to test numbers.
    Pass send_now: true to auto-deliver after approval."""
    data = request.json or {}
    send_now = data.get('send_now', False)
    
    if data.get('auto'):
        result = campaign_service.approve_campaigns(auto=True, send_now=send_now)
    elif data.get('state'):
        result = campaign_service.approve_campaigns(state=data['state'], send_now=send_now)
    elif data.get('campaign_ids'):
        result = campaign_service.approve_campaigns(campaign_ids=data['campaign_ids'], send_now=send_now)
    else:
        return jsonify({'error': 'Provide auto=true, state, or campaign_ids'}), 400
    
    return jsonify(result)


@campaign_bp.route('/api/campaign/<int:campaign_id>', methods=['GET'])
def get_campaign_detail(campaign_id):
    """Get full campaign details including content"""
    from database import get_db
    conn = get_db()
    campaign = conn.execute("""
        SELECT c.*, d.state, d.district as district_name, d.latitude, d.longitude
        FROM campaigns c
        JOIN districts d ON c.district_id = d.id
        WHERE c.id = ?
    """, (campaign_id,)).fetchone()
    conn.close()
    
    if not campaign:
        return jsonify({'error': 'Campaign not found'}), 404
    
    return jsonify(dict(campaign))


@campaign_bp.route('/api/campaign/<int:campaign_id>/voice', methods=['POST'])
def generate_voice(campaign_id):
    """Generate voice file for a campaign"""
    from database import get_db
    conn = get_db()
    campaign = conn.execute(
        "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
    ).fetchone()
    conn.close()
    
    if not campaign:
        return jsonify({'error': 'Campaign not found'}), 404
    
    if not campaign['message_voice_script']:
        return jsonify({'error': 'No voice script'}), 400
    
    voice_path = campaign_service.voice_service.generate_voice_sync(
        campaign['message_voice_script'],
        campaign['language'],
        campaign_id
    )
    
    if voice_path:
        conn = get_db()
        conn.execute(
            "UPDATE campaigns SET voice_file_path=? WHERE id=?",
            (voice_path, campaign_id)
        )
        conn.commit()
        conn.close()
        return jsonify({'voice_file': voice_path, 'status': 'generated'})
    
    return jsonify({'error': 'Voice generation failed'}), 500


@campaign_bp.route('/api/campaign/demo/instant', methods=['GET'])
def instant_demo():
    """
    INSTANT DEMO — shows pre-cached campaigns from DB.
    No API calls, no weather fetch, no ML prediction.
    Run /api/campaign/demo BEFORE the presentation to pre-load data.
    Then use this endpoint during demo for INSTANT results.
    """
    from database import get_db
    conn = get_db()
    campaigns = conn.execute("""
        SELECT c.*, d.state, d.district as district_name, d.language
        FROM campaigns c
        JOIN districts d ON c.district_id = d.id
        ORDER BY c.created_at DESC LIMIT 20
    """).fetchall()
    
    stats = {
        'total': conn.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0],
        'pending': conn.execute("SELECT COUNT(*) FROM campaigns WHERE status='pending'").fetchone()[0],
        'approved': conn.execute("SELECT COUNT(*) FROM campaigns WHERE status='approved'").fetchone()[0],
        'completed': conn.execute("SELECT COUNT(*) FROM campaigns WHERE status='completed'").fetchone()[0],
    }
    conn.close()
    
    return jsonify({
        'mode': 'instant_demo',
        'note': 'Pre-cached data — no API calls made',
        'stats': stats,
        'campaigns': [dict(c) for c in campaigns]
    })

