import sqlite3

conn = sqlite3.connect('krishiconnect.db')
conn.execute('DELETE FROM campaigns')
conn.execute('DELETE FROM predictions')
conn.execute('DELETE FROM delivery_log')
conn.execute('DELETE FROM weather_cache')
conn.commit()

print("ALL DATA CLEARED - Fresh start!")
print(f"Districts: {conn.execute('SELECT COUNT(*) FROM districts').fetchone()[0]}")
print(f"Campaigns: {conn.execute('SELECT COUNT(*) FROM campaigns').fetchone()[0]}")
conn.close()
print("Ready for demo!")
