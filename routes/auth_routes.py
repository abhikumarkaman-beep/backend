# KrishiConnect AI - Auth Routes (Register, Login, Admin User Management)
import hashlib
from flask import Blueprint, jsonify, request
from database import get_db

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/api/auth/register', methods=['POST'])
def register():
    """Register a new employee — status will be 'pending' until admin approves"""
    data = request.json or {}
    name = data.get('name', '').strip()
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    department = data.get('department', '').strip()
    
    if not name or not email or not password:
        return jsonify({'error': 'Name, email, and password are required'}), 400
    
    if len(password) < 3:
        return jsonify({'error': 'Password must be at least 3 characters'}), 400
    
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO users (name, email, password_hash, role, status, department)
            VALUES (?, ?, ?, 'employee', 'pending', ?)
        """, (name, email, password_hash, department))
        conn.commit()
        conn.close()
        return jsonify({
            'success': True,
            'message': 'Registration successful! Please wait for admin approval before logging in.'
        })
    except Exception as e:
        conn.close()
        if 'UNIQUE' in str(e):
            return jsonify({'error': 'Email already registered'}), 400
        return jsonify({'error': str(e)}), 500


@auth_bp.route('/api/auth/login', methods=['POST'])
def login():
    """Login — only approved users can login"""
    data = request.json or {}
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    
    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400
    
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    
    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE email = ?", (email,)
    ).fetchone()
    conn.close()
    
    if not user:
        return jsonify({'error': 'Invalid email or password'}), 401
    
    if user['password_hash'] != password_hash:
        return jsonify({'error': 'Invalid email or password'}), 401
    
    if user['status'] == 'pending':
        return jsonify({'error': 'Your account is pending admin approval. Please wait.'}), 403
    
    if user['status'] == 'rejected':
        return jsonify({'error': 'Your account has been rejected. Contact administrator.'}), 403
    
    return jsonify({
        'success': True,
        'user': {
            'id': user['id'],
            'name': user['name'],
            'email': user['email'],
            'role': user['role'],
            'department': user['department'],
        }
    })


@auth_bp.route('/api/admin/users', methods=['GET'])
def list_users():
    """Admin: List all users with optional status filter"""
    status = request.args.get('status')
    conn = get_db()
    
    if status:
        users = conn.execute(
            "SELECT id, name, email, role, status, department, created_at, approved_by, approved_at FROM users WHERE status = ? ORDER BY created_at DESC",
            (status,)
        ).fetchall()
    else:
        users = conn.execute(
            "SELECT id, name, email, role, status, department, created_at, approved_by, approved_at FROM users ORDER BY created_at DESC"
        ).fetchall()
    
    conn.close()
    return jsonify({
        'users': [dict(u) for u in users],
        'total': len(users),
        'pending': sum(1 for u in users if u['status'] == 'pending'),
    })


@auth_bp.route('/api/admin/users/<int:user_id>/approve', methods=['POST'])
def approve_user(user_id):
    """Admin: Approve a pending user"""
    conn = get_db()
    conn.execute("""
        UPDATE users SET status='approved', approved_by='admin', approved_at=CURRENT_TIMESTAMP
        WHERE id = ? AND status = 'pending'
    """, (user_id,))
    changes = conn.execute("SELECT changes()").fetchone()[0]
    conn.commit()
    conn.close()
    
    if changes == 0:
        return jsonify({'error': 'User not found or already processed'}), 404
    return jsonify({'success': True, 'message': 'User approved successfully'})


@auth_bp.route('/api/admin/users/<int:user_id>/reject', methods=['POST'])
def reject_user(user_id):
    """Admin: Reject a pending user"""
    conn = get_db()
    conn.execute("""
        UPDATE users SET status='rejected', approved_by='admin', approved_at=CURRENT_TIMESTAMP
        WHERE id = ? AND status = 'pending'
    """, (user_id,))
    changes = conn.execute("SELECT changes()").fetchone()[0]
    conn.commit()
    conn.close()
    
    if changes == 0:
        return jsonify({'error': 'User not found or already processed'}), 404
    return jsonify({'success': True, 'message': 'User rejected'})


@auth_bp.route('/api/admin/users/<int:user_id>', methods=['DELETE'])
def delete_user(user_id):
    """Admin: Delete a user"""
    conn = get_db()
    conn.execute("DELETE FROM users WHERE id = ? AND role != 'admin'", (user_id,))
    changes = conn.execute("SELECT changes()").fetchone()[0]
    conn.commit()
    conn.close()
    
    if changes == 0:
        return jsonify({'error': 'User not found or is admin'}), 404
    return jsonify({'success': True, 'message': 'User deleted'})


@auth_bp.route('/api/admin/reset', methods=['POST'])
def system_reset():
    """Admin: Clear all pipeline output data (campaigns, predictions, delivery logs, weather cache)"""
    data = request.json or {}
    confirm = data.get('confirm', '')
    
    if confirm != 'RESET':
        return jsonify({'error': 'Type RESET to confirm'}), 400
    
    conn = get_db()
    
    # Count before clearing
    counts = {
        'campaigns': conn.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0],
        'predictions': conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0],
        'delivery_logs': conn.execute("SELECT COUNT(*) FROM delivery_log").fetchone()[0],
        'weather_cache': conn.execute("SELECT COUNT(*) FROM weather_cache").fetchone()[0],
        'ndvi_alerts': conn.execute("SELECT COUNT(*) FROM ndvi_alerts").fetchone()[0],
        'feedback': conn.execute("SELECT COUNT(*) FROM farmer_feedback").fetchone()[0],
    }
    
    # Clear pipeline output tables
    conn.execute("DELETE FROM delivery_log")
    conn.execute("DELETE FROM farmer_feedback")
    conn.execute("DELETE FROM campaigns")
    conn.execute("DELETE FROM predictions")
    conn.execute("DELETE FROM weather_cache")
    conn.execute("DELETE FROM ndvi_alerts")
    conn.execute("DELETE FROM ab_tests")
    
    conn.commit()
    conn.close()
    
    # Cleanup media files (Cloudinary + local)
    media_deleted = {'cloudinary': 0, 'local': 0}
    try:
        from services.media_service import MediaService
        media = MediaService()
        media_deleted = media.cleanup_all()
    except Exception as e:
        print(f"[RESET] Media cleanup error: {e}")
    
    # Clear inventory cache so page shows fresh data
    try:
        from services.inventory_service import InventoryService
        InventoryService._cache.clear()
    except: pass
    
    return jsonify({
        'success': True,
        'message': 'System reset complete. Pipeline data + media files cleared.',
        'cleared': counts,
        'media_deleted': media_deleted,
    })

