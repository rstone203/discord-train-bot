import os
import time
import psycopg2

db_url = os.getenv('DATABASE_URL')
if not db_url:
    print("No DATABASE_URL found, skipping DB wake")
    exit(0)

for attempt in range(10):
    try:
        conn = psycopg2.connect(db_url, connect_timeout=30)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
        print(f"Database is awake (attempt {attempt + 1})")
        exit(0)
    except Exception as e:
        if "disabled" in str(e).lower() or "starting up" in str(e).lower():
            wait = min(10 * (attempt + 1), 30)
            print(f"Database sleeping, waiting {wait}s... (attempt {attempt + 1}/10)")
            time.sleep(wait)
        else:
            print(f"DB connection error: {e}")
            time.sleep(5)

print("WARNING: Could not wake database after 10 attempts")
exit(1)
