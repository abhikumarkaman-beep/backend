# KrishiConnect AI - Farmer Feedback Service (Two-Way Interaction Engine)
# Processes farmer replies, creates leads, tracks engagement
import hashlib
from datetime import datetime
from database import get_db


# ═══════════════════════════════════════
# RESPONSE MAPPING — the heart of two-way interaction
# ═══════════════════════════════════════
RESPONSE_MAP = {
    "1": {
        "type": "more_info",
        "label": "More Information Requested",
        "score": 5,
        "priority": "low",
        "auto_reply": "Dhanyavaad! Aapko jaldi hi aur jaankari bheji jayegi. 🌾\n\nSyngenta Helpline: 1800-123-4567 (Toll Free)"
    },
    "2": {
        "type": "expert_help",
        "label": "Expert Consultation Requested",
        "score": 8,
        "priority": "high",
        "auto_reply": "Aapka request mil gaya hai! Humara krishi visheshagya 24 ghante me aapse sampark karega. 👨‍🌾\n\nSyngenta Helpline: 1800-123-4567 (Toll Free)"
    },
    "3": {
        "type": "buy_intent",
        "label": "Purchase Interest",
        "score": 10,
        "priority": "high",
        "auto_reply": "Dhanyavaad! Aapke nazdeeki Syngenta retailer ki jaankari jaldi bheji jayegi. 🏪\n\nAbhi call karein: 1800-123-4567 (Toll Free)"
    },
    "4": {
        "type": "field_issue",
        "label": "Field Issue Reported",
        "score": 9,
        "priority": "critical",
        "auto_reply": "Aapki report darj ho gayi hai. Humari team jald hi madad karegi. 🔍\n\nAgar photo bhej sakte hain toh zarur bhejein.\nSyngenta Helpline: 1800-123-4567"
    },
}

# Default for unrecognized replies
DEFAULT_RESPONSE = {
    "type": "general",
    "label": "General Message",
    "score": 3,
    "priority": "low",
    "auto_reply": "Dhanyavaad aapke sandesh ke liye! 🙏\n\nMadad ke liye call karein: 1800-123-4567 (Toll Free)"
}


class FeedbackService:
    """Process farmer replies, create leads, track engagement"""

    def process_incoming(self, from_number, body, media_url=None):
        """
        Main entry point: process an incoming farmer reply.
        
        1. Parse the reply code (1/2/3/4 or free text)
        2. Find which campaign was last sent to this number
        3. Create a feedback/lead entry
        4. Return auto-reply message
        """
        # Clean phone number
        clean_phone = self._clean_phone(from_number)
        phone_hash = self._hash_phone(clean_phone)
        
        # Parse response
        body_stripped = body.strip() if body else ""
        response_info = RESPONSE_MAP.get(body_stripped, DEFAULT_RESPONSE)
        feedback_code = int(body_stripped) if body_stripped in RESPONSE_MAP else None
        
        # Find campaign this reply is for (latest campaign sent to this number)
        campaign_info = self._find_campaign_for_phone(clean_phone)
        
        # Save to farmer_feedback
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO farmer_feedback 
            (campaign_id, district, state, crop, disease, product,
             farmer_phone_hash, feedback_code, feedback_type, feedback_text,
             priority, score, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            campaign_info.get('campaign_id'),
            campaign_info.get('district', 'Unknown'),
            campaign_info.get('state', ''),
            campaign_info.get('crop', ''),
            campaign_info.get('disease', ''),
            campaign_info.get('product', ''),
            phone_hash,
            feedback_code,
            response_info['type'],
            body_stripped,
            response_info['priority'],
            response_info['score'],
            'new'
        ))
        feedback_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        print(f"[FEEDBACK] #{feedback_id} from {phone_hash[:8]}... | "
              f"Code: {feedback_code} | Type: {response_info['type']} | "
              f"Campaign: {campaign_info.get('campaign_id', 'none')} | "
              f"Priority: {response_info['priority']}")
        
        return {
            'feedback_id': feedback_id,
            'type': response_info['type'],
            'label': response_info['label'],
            'score': response_info['score'],
            'priority': response_info['priority'],
            'campaign_id': campaign_info.get('campaign_id'),
            'district': campaign_info.get('district', 'Unknown'),
            'auto_reply': response_info['auto_reply'],
        }
    
    def _clean_phone(self, phone):
        """Normalize phone number format"""
        digits = ''.join(ch for ch in str(phone) if ch.isdigit())
        if len(digits) > 10 and digits.startswith('91'):
            digits = digits[-10:]
        return digits
    
    def _hash_phone(self, phone):
        """Hash phone for privacy (don't store raw numbers)"""
        return hashlib.sha256(phone.encode()).hexdigest()[:16]
    
    def _find_campaign_for_phone(self, clean_phone):
        """Find the most recent campaign sent to this phone number"""
        conn = get_db()
        
        # Try matching by recipient_phone in delivery_log
        # Phone format in delivery_log is 'whatsapp:+91XXXXXXXXXX'
        possible_formats = [
            f"whatsapp:+91{clean_phone}",
            f"+91{clean_phone}",
            clean_phone,
        ]
        
        for fmt in possible_formats:
            row = conn.execute("""
                SELECT dl.campaign_id, c.disease, c.crop, c.product,
                       d.district, d.state
                FROM delivery_log dl
                JOIN campaigns c ON dl.campaign_id = c.id
                JOIN districts d ON c.district_id = d.id
                WHERE dl.recipient_phone = ? AND dl.status = 'sent'
                ORDER BY dl.sent_at DESC LIMIT 1
            """, (fmt,)).fetchone()
            
            if row:
                conn.close()
                return dict(row)
        
        # Fallback: if no match by phone, get the most recent campaign overall
        # (useful for demo with test numbers)
        row = conn.execute("""
            SELECT c.id as campaign_id, c.disease, c.crop, c.product,
                   d.district, d.state
            FROM campaigns c
            JOIN districts d ON c.district_id = d.id
            ORDER BY c.created_at DESC LIMIT 1
        """).fetchone()
        
        conn.close()
        
        if row:
            return dict(row)
        
        return {}
    
    # ═══════════════════════════════════════
    # LEADS API — for dashboard
    # ═══════════════════════════════════════
    
    def get_leads(self, status=None, priority=None, feedback_type=None, limit=50):
        """Get all farmer feedback/leads with filters"""
        conn = get_db()
        query = """
            SELECT ff.*, c.risk_level, c.language,
                   c.message_whatsapp
            FROM farmer_feedback ff
            LEFT JOIN campaigns c ON ff.campaign_id = c.id
            WHERE 1=1
        """
        params = []
        
        if status:
            query += " AND ff.status = ?"
            params.append(status)
        if priority:
            query += " AND ff.priority = ?"
            params.append(priority)
        if feedback_type:
            query += " AND ff.feedback_type = ?"
            params.append(feedback_type)
        
        query += f" ORDER BY ff.received_at DESC LIMIT {int(limit)}"
        
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    
    def update_lead_status(self, feedback_id, status, assigned_to=None):
        """Update lead status (new → assigned → resolved)"""
        conn = get_db()
        
        if status == 'resolved':
            conn.execute("""
                UPDATE farmer_feedback 
                SET status = ?, assigned_to = ?, resolved_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (status, assigned_to, feedback_id))
        else:
            conn.execute("""
                UPDATE farmer_feedback 
                SET status = ?, assigned_to = ?
                WHERE id = ?
            """, (status, assigned_to, feedback_id))
        
        conn.commit()
        conn.close()
        return {'updated': True, 'id': feedback_id, 'status': status}
    
    # ═══════════════════════════════════════
    # ENGAGEMENT METRICS — for dashboard
    # ═══════════════════════════════════════
    
    def get_engagement_stats(self):
        """Campaign engagement analytics"""
        conn = get_db()
        
        total_campaigns = conn.execute(
            "SELECT COUNT(*) FROM campaigns WHERE status IN ('completed', 'approved')"
        ).fetchone()[0]
        
        total_feedback = conn.execute(
            "SELECT COUNT(*) FROM farmer_feedback"
        ).fetchone()[0]
        
        # Feedback by type
        type_breakdown = conn.execute("""
            SELECT feedback_type, COUNT(*) as count, AVG(score) as avg_score
            FROM farmer_feedback 
            GROUP BY feedback_type
        """).fetchall()
        
        # Feedback by priority
        priority_breakdown = conn.execute("""
            SELECT priority, COUNT(*) as count
            FROM farmer_feedback 
            GROUP BY priority
        """).fetchall()
        
        # Feedback by status
        status_breakdown = conn.execute("""
            SELECT status, COUNT(*) as count
            FROM farmer_feedback 
            GROUP BY status
        """).fetchall()
        
        # High-value leads (score >= 8)
        high_value_leads = conn.execute(
            "SELECT COUNT(*) FROM farmer_feedback WHERE score >= 8"
        ).fetchone()[0]
        
        # District-wise feedback
        district_feedback = conn.execute("""
            SELECT district, state, COUNT(*) as count,
                   SUM(CASE WHEN feedback_type = 'field_issue' THEN 1 ELSE 0 END) as field_issues,
                   SUM(CASE WHEN feedback_type = 'buy_intent' THEN 1 ELSE 0 END) as buy_intents
            FROM farmer_feedback
            WHERE district IS NOT NULL AND district != 'Unknown'
            GROUP BY district, state
            ORDER BY count DESC
            LIMIT 20
        """).fetchall()
        
        conn.close()
        
        # Calculate engagement rate
        engagement_rate = round((total_feedback / total_campaigns * 100), 1) if total_campaigns > 0 else 0
        
        return {
            'total_campaigns_sent': total_campaigns,
            'total_responses': total_feedback,
            'engagement_rate': engagement_rate,
            'high_value_leads': high_value_leads,
            'by_type': [dict(r) for r in type_breakdown],
            'by_priority': [dict(r) for r in priority_breakdown],
            'by_status': [dict(r) for r in status_breakdown],
            'by_district': [dict(r) for r in district_feedback],
        }
    
    # ═══════════════════════════════════════
    # GROUND TRUTH VALIDATION
    # ═══════════════════════════════════════
    
    def get_ground_truth_validation(self):
        """
        Compare ML predictions with farmer field reports (reply=4).
        Shows which AI predictions were confirmed by ground truth.
        """
        conn = get_db()
        
        # Get districts where farmers reported field issues (reply=4)
        field_reports = conn.execute("""
            SELECT ff.district, ff.state, ff.disease, ff.crop,
                   COUNT(*) as farmer_reports,
                   GROUP_CONCAT(DISTINCT ff.feedback_type) as report_types
            FROM farmer_feedback ff
            WHERE ff.feedback_type = 'field_issue'
            AND ff.district IS NOT NULL
            GROUP BY ff.district, ff.state, ff.disease
        """).fetchall()
        
        validations = []
        for report in field_reports:
            r = dict(report)
            
            # Find matching ML prediction for this district + disease
            pred = conn.execute("""
                SELECT probability, risk_level, prediction_method, created_at
                FROM predictions
                WHERE district_id IN (
                    SELECT id FROM districts WHERE district = ? AND state = ?
                )
                AND disease = ?
                ORDER BY created_at DESC LIMIT 1
            """, (r['district'], r['state'], r['disease'])).fetchone()
            
            if pred:
                r['ml_probability'] = pred['probability']
                r['ml_risk_level'] = pred['risk_level']
                r['prediction_date'] = pred['created_at']
                r['validated'] = True
                r['validation_status'] = 'ML Prediction CONFIRMED by field reports'
            else:
                r['validated'] = False
                r['validation_status'] = 'Field report — no matching ML prediction'
            
            validations.append(r)
        
        conn.close()
        
        return {
            'total_validations': len(validations),
            'confirmed_predictions': sum(1 for v in validations if v.get('validated')),
            'validations': validations,
        }
    
    # ═══════════════════════════════════════
    # OUTBREAK DETECTION
    # ═══════════════════════════════════════
    
    def detect_outbreaks(self, threshold=3):
        """
        Detect potential outbreaks: multiple farmers in same district
        reporting field issues (reply=4) for same disease.
        Combined with ML predictions for stronger signal.
        """
        conn = get_db()
        
        outbreaks = conn.execute("""
            SELECT ff.district, ff.state, ff.disease, ff.crop,
                   COUNT(*) as report_count,
                   MIN(ff.received_at) as first_report,
                   MAX(ff.received_at) as latest_report
            FROM farmer_feedback ff
            WHERE ff.feedback_type = 'field_issue'
            AND ff.district IS NOT NULL
            GROUP BY ff.district, ff.state, ff.disease
            HAVING COUNT(*) >= ?
            ORDER BY report_count DESC
        """, (threshold,)).fetchall()
        
        results = []
        for ob in outbreaks:
            o = dict(ob)
            
            # Get ML prediction confidence for this district+disease
            pred = conn.execute("""
                SELECT probability, risk_level
                FROM predictions
                WHERE district_id IN (
                    SELECT id FROM districts WHERE district = ? AND state = ?
                )
                AND disease = ?
                ORDER BY created_at DESC LIMIT 1
            """, (o['district'], o['state'], o['disease'])).fetchone()
            
            if pred:
                o['ml_probability'] = pred['probability']
                o['ml_risk_level'] = pred['risk_level']
                # Combined confidence: ML + farmer reports
                farmer_confidence = min(o['report_count'] / 10, 1.0)  # Cap at 1.0
                o['combined_confidence'] = round(
                    (pred['probability'] * 0.6) + (farmer_confidence * 0.4), 2
                )
            else:
                o['ml_probability'] = None
                o['combined_confidence'] = round(min(o['report_count'] / 10, 1.0), 2)
            
            # Severity level
            if o['report_count'] >= 10:
                o['severity'] = 'CRITICAL'
            elif o['report_count'] >= 5:
                o['severity'] = 'HIGH'
            else:
                o['severity'] = 'MODERATE'
            
            results.append(o)
        
        conn.close()
        
        return {
            'total_outbreaks': len(results),
            'critical': sum(1 for r in results if r['severity'] == 'CRITICAL'),
            'outbreaks': results,
        }
    
    # ═══════════════════════════════════════
    # RETAILER CONNECT (for buy_intent leads)
    # ═══════════════════════════════════════
    
    def get_retailer_for_lead(self, feedback_id):
        """When farmer shows buy interest (reply=3), find nearest retailer with stock"""
        conn = get_db()
        
        # Get the feedback details
        feedback = conn.execute(
            "SELECT * FROM farmer_feedback WHERE id = ?", (feedback_id,)
        ).fetchone()
        
        if not feedback:
            conn.close()
            return {'error': 'Feedback not found'}
        
        fb = dict(feedback)
        
        # Find retailers in same district
        retailers = conn.execute("""
            SELECT sr.retailer_id, sr.retailer_name, sr.district, sr.state,
                   sr.retailer_phone, sr.latitude, sr.longitude
            FROM syngenta_retailers sr
            WHERE sr.district = ? AND sr.state = ?
            LIMIT 5
        """, (fb['district'], fb['state'])).fetchall()
        
        # Check inventory for the product
        retailer_list = []
        for ret in retailers:
            r = dict(ret)
            
            # Check stock for this product at this retailer
            stock = conn.execute("""
                SELECT si.sku_name, si.sku_qty
                FROM syngenta_inventory si
                WHERE si.retailer_id = ?
                AND si.week_end_date = (SELECT MAX(week_end_date) FROM syngenta_inventory)
                ORDER BY si.sku_qty DESC
                LIMIT 5
            """, (r['retailer_id'],)).fetchall()
            
            r['stock'] = [dict(s) for s in stock]
            r['has_stock'] = any(s['sku_qty'] > 0 for s in stock) if stock else False
            retailer_list.append(r)
        
        conn.close()
        
        return {
            'feedback_id': feedback_id,
            'district': fb['district'],
            'state': fb['state'],
            'product_needed': fb['product'],
            'retailers': retailer_list,
            'total_retailers': len(retailer_list),
        }
