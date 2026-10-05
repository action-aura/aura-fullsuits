"""v44 diagnostic: stored `credit_balance` vs the figure DERIVED from a retail
database's own sales / payments / returns / purchase orders.

    python scripts/diag/party_balance_check.py <retail.db> [--all]

READ-ONLY by construction: the file is opened with SQLite's `mode=ro` URI, so
the tool cannot repair, refresh or otherwise change what it measures. Point it
at a COPY of a database you care about anyway (a `-wal` file beside a live
database is only read, but a copy removes the question).

Output: one block per company -- every party whose stored figure differs from
the derived one (all parties with `--all`), then the stored and derived totals
of each side. Exit code 0 always (it is a measurement, not a gate).
"""
import argparse
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / 'products' / 'retail' / 'backend')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.retail import party_balance  # noqa: E402


def _companies(conn):
    seen = []
    for table in ('customers', 'suppliers'):
        try:
            for (cid,) in conn.execute(f"SELECT DISTINCT company_id FROM {table}").fetchall():
                if cid not in seen:
                    seen.append(cid)
        except sqlite3.DatabaseError:
            pass
    return seen


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('db')
    ap.add_argument('--all', action='store_true', help='list parties that agree too')
    args = ap.parse_args(argv)
    path = os.path.abspath(args.db)
    if not os.path.exists(path):
        print(f"no such file: {path}")
        return 2
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    print(f"database: {path}  user_version={conn.execute('PRAGMA user_version').fetchone()[0]}")
    for cid in _companies(conn):
        currency = party_balance._currency_of(conn, cid)
        everyone = party_balance.drift_report(conn, cid, currency, include_equal=True)
        eps = party_balance.money_epsilon(currency)
        shown = everyone if args.all else [r for r in everyone if abs(r['diff']) > eps]
        print(f"\ncompany {cid}  currency={currency}  parties with a figure={len(everyone)}  drifted={sum(1 for r in everyone if abs(r['diff']) > eps)}")
        for r in shown:
            print(f"  {r['party_type']:<8} {r['party_id'][:12]:<12} {str(r['name'])[:28]:<28} "
                  f"stored={r['stored']:>12.3f} derived={r['derived']:>12.3f} diff={r['diff']:>12.3f}")
        for kind in ('customer', 'supplier'):
            rows = [r for r in everyone if r['party_type'] == kind]
            print(f"  TOTAL {kind:<8} stored={sum(r['stored'] for r in rows):>12.3f} derived={sum(r['derived'] for r in rows):>12.3f}")
    conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
