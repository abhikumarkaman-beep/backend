# KrishiConnect AI - Campaign API Routes
from flask import Blueprint, jsonify, request, Response
from services.campaign_service import CampaignService
import json
import threading
import uuid
from datetime import datetime

campaign_bp = Blueprint('campaign', __name__)
campaign_service = CampaignService()
_pipeline_jobs = {}
_pipeline_jobs_lock = threading.Lock()


def _empty_pipeline_result():
    return {
        'district_health': [],
        'campaigns': [],
        'healthy': 0,
        'at_risk': 0,
        'campaigns_created': 0,
        'total_districts': 0,
        'season': '',
        'state_filter': '',
        'mode': 'standard',
    }


def _set_job(run_id, **updates):
    with _pipeline_jobs_lock:
        job = _pipeline_jobs.get(run_id)
        if not job:
            return
        job.update(updates)
        job['updated_at'] = datetime.utcnow().isoformat() + 'Z'


def _run_pipeline_job(run_id, state=None, limit=1000, syngenta_only=False):
    try:
        for event in campaign_service.run_pipeline_stream(
            state=state, limit=limit, syngenta_only=syngenta_only
        ):
            event_type = event.get('type')
            with _pipeline_jobs_lock:
                job = _pipeline_jobs.get(run_id)
                if not job:
                    return

                job['events'].append(event)
                job['updated_at'] = datetime.utcnow().isoformat() + 'Z'

                if event_type == 'error':
                    job['status'] = 'error'
                    job['error'] = event.get('message', 'Pipeline failed')
                    job['running'] = False
                    return

                if event_type == 'init':
                    result = job['result']
                    result['season'] = event.get('season', '')
                    result['state_filter'] = event.get('state_filter', state or 'All India')
                    result['total_districts'] = event.get('total', 0)
                    result['mode'] = event.get('mode', 'standard')
                    job['status'] = 'running'
                    job['total'] = event.get('total', 0)

                elif event_type == 'phase':
                    job['phase'] = event.get('message', '')

                elif event_type == 'district':
                    data = event.get('data') or {}
                    result = job['result']
                    result['district_health'].append(data)
                    if data.get('status') == 'healthy':
                        result['healthy'] += 1
                    elif data.get('status') == 'at_risk':
                        result['at_risk'] += 1
                    job['progress'] = event.get('progress', job['progress'])
                    job['total'] = event.get('total', job['total'])
                    job['latest'] = f"{data.get('district', '')}, {data.get('state', '')}".strip(', ')

                elif event_type == 'complete':
                    summary = event.get('summary') or {}
                    result = job['result']
                    result['campaigns_created'] = summary.get('campaigns_created', 0)
                    result['total_districts'] = summary.get('total', result.get('total_districts', 0))
                    job['summary'] = summary
                    job['progress'] = summary.get('total', job['progress'])
                    job['total'] = summary.get('total', job['total'])
                    job['phase'] = f"Complete - {job['total']} districts"
                    job['status'] = 'complete'
                    job['running'] = False
                    return

        _set_job(run_id, status='complete', running=False)
    except Exception as e:
        _set_job(run_id, status='error', running=False, error=str(e))


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


@campaign_bp.route('/api/campaign/start', methods=['POST'])
def start_pipeline_job():
    """
    Production-safe pipeline entrypoint.
    Starts work in a background thread and returns immediately, so Vercel/Render
    do not need to keep one long SSE request alive for large states.
    """
    data = request.json or {}
    state = data.get('state') or None
    limit = int(data.get('limit', 1000))
    syngenta_only = bool(data.get('syngenta_only', False))

    if campaign_service.is_pipeline_running():
        return jsonify({'error': 'Pipeline already running! Please wait.'}), 409

    run_id = str(uuid.uuid4())[:12]
    now = datetime.utcnow().isoformat() + 'Z'
    with _pipeline_jobs_lock:
        _pipeline_jobs[run_id] = {
            'run_id': run_id,
            'status': 'queued',
            'running': True,
            'progress': 0,
            'total': 0,
            'phase': 'Queued pipeline...',
            'latest': '',
            'error': None,
            'summary': None,
            'events': [],
            'result': _empty_pipeline_result(),
            'created_at': now,
            'updated_at': now,
            'params': {
                'state': state,
                'limit': limit,
                'syngenta_only': syngenta_only,
            },
        }

    worker = threading.Thread(
        target=_run_pipeline_job,
        kwargs={'run_id': run_id, 'state': state, 'limit': limit, 'syngenta_only': syngenta_only},
        daemon=True,
    )
    worker.start()

    return jsonify({'run_id': run_id, 'status': 'queued'})


@campaign_bp.route('/api/campaign/status/<run_id>', methods=['GET'])
def pipeline_job_status(run_id):
    """Return current background pipeline progress."""
    with _pipeline_jobs_lock:
        job = _pipeline_jobs.get(run_id)
        if not job:
            return jsonify({'error': 'Pipeline run not found'}), 404
        return jsonify({
            'run_id': run_id,
            'status': job['status'],
            'running': job['running'],
            'progress': job['progress'],
            'total': job['total'],
            'phase': job['phase'],
            'latest': job['latest'],
            'error': job['error'],
            'summary': job['summary'],
            'result': job['result'],
            'created_at': job['created_at'],
            'updated_at': job['updated_at'],
        })


@campaign_bp.route('/api/campaign/results/<run_id>', methods=['GET'])
def pipeline_job_results(run_id):
    """Return accumulated/final background pipeline result."""
    with _pipeline_jobs_lock:
        job = _pipeline_jobs.get(run_id)
        if not job:
            return jsonify({'error': 'Pipeline run not found'}), 404
        return jsonify({
            'run_id': run_id,
            'status': job['status'],
            'running': job['running'],
            'result': job['result'],
            'summary': job['summary'],
            'error': job['error'],
        })


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
        for event in campaign_service.run_pipeline_stream_fast(
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
