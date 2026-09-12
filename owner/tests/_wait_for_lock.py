import sys
import time

import psycopg

DSN = "postgresql://aura_owner:aura_owner_dev@localhost:5432/aura_owner_test"
LOCK_KEY = 1098216033

deadline = time.time() + 25 * 60
while time.time() < deadline:
    conn = psycopg.connect(DSN, autocommit=True)
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND granted AND objid=%s", (LOCK_KEY,))
    held = cur.fetchone()[0]
    conn.close()
    if held == 0:
        print("LOCK-FREE", flush=True)
        sys.exit(0)
    time.sleep(20)

print("TIMEOUT-STILL-HELD", flush=True)
sys.exit(1)
