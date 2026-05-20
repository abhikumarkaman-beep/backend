import sys
sys.path.insert(0, '.')
from database import get_db
import hashlib

conn = get_db()
conn.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT DEFAULT 'employee',
    status TEXT DEFAULT 'pending',
    department TEXT,
    approved_by TEXT,
    approved_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")

h = hashlib.sha256('123'.encode()).hexdigest()
conn.execute(
    "INSERT OR IGNORE INTO users (name, email, password_hash, role, status) VALUES (?, ?, ?, 'admin', 'approved')",
    ('Admin', 'ram@gmail.com', h)
)
conn.commit()

users = conn.execute("SELECT id, name, email, role, status FROM users").fetchall()
print(f"Users: {len(users)}")
for u in users:
    print(dict(u))
conn.close()
