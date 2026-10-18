"""Runs RETENTION_SQL from retention/index.ts against a real SQLite table:
a lead 23 months old must survive, one 25 months old must be deleted.
D1 is SQLite, so datetime() semantics are identical. Run: py -3.14 tools/check_retention_sql.py"""
import re, sqlite3, sys, pathlib
src = pathlib.Path('retention/index.ts').read_text(encoding='utf-8')
sql = re.search(r'RETENTION_SQL = "([^"]+)"', src).group(1)
db = sqlite3.connect(':memory:')
db.executescript(pathlib.Path('migrations/0001_leads.sql').read_text(encoding='utf-8'))
info = list(db.execute('PRAGMA table_info(leads)'))
cols = [r[1] for r in info]
required = {r[1] for r in info if r[3]}  # NOT NULL columns get '' unless set below
def add(age):
    vals = {c: ('' if c in required else None) for c in cols if c != 'id'}
    created = db.execute(f"SELECT datetime('now','{age}')").fetchone()[0]
    vals.update(created_at=created, name='t', phone='+962790000000', lang='en', ip_hash='x')
    db.execute(f"INSERT INTO leads ({','.join(vals)}) VALUES ({','.join('?'*len(vals))})", list(vals.values()))
    return created
t23 = add('-23 months'); t25 = add('-25 months'); t1 = add('-1 days')
db.execute(sql)
left = sorted(r[0] for r in db.execute('SELECT created_at FROM leads'))
checks = {
    '25-month lead deleted': t25 not in left,
    '23-month lead kept': t23 in left,
    '1-day lead kept': t1 in left,
    'exactly 2 rows left': len(left) == 2,
}
ok = all(checks.values())
for name, passed in checks.items():
    if not passed: print('FAILED expectation:', name)
print('kept', left, 'OK' if ok else 'FAIL'); sys.exit(0 if ok else 1)
