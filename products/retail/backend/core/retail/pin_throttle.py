"""
Aura Retail -- persistent, race-proof failure counter for manager-PIN approvals (UI-3a).

A manager PIN is four digits: 10,000 possibilities. The KDF behind it
(commercial_runtime.identity.user_accounts.verify_user_pin, PBKDF2 at 600k rounds) slows each
guess to a few hundred milliseconds, but a script can still sweep the space through the sale
endpoint unless something stops it. This is that something.

WHY THIS REPLACED THE FIRST VERSION (verifier finding HIGH-1, 2026-10-13). The first version
was an in-process dict: it checked the lock, ran the slow KDF, and only THEN recorded the
failure. Measured on waitress (12 threads): 15 wrong PINs were evaluated in one burst against a
design of 5, because every request in flight passed the lock check before any failure was
counted; a success wiped failures counted by concurrent attempts; and a restart of the app
cleared the lock (the attacker IS the cashier sitting at that till). Three fixes, all here:

  1. COUNT FIRST. `reserve_attempt` increments the approver's failure counter inside one
     `BEGIN IMMEDIATE` transaction BEFORE the PIN is looked at. The write lock serialises
     concurrent requests on the counter, so the 6th request in a window is refused no matter
     how many are in flight. At most MAX_FAILURES PINs are ever *verified* per approver per window.
  2. A SUCCESS ONLY UNDOES ITS OWN INCREMENT. `release_attempt` decrements by one (floor 0). It
     never wipes failures that other attempts counted.
  3. THE COUNTER LIVES IN THE RETAIL DATABASE (`pin_approval_attempts`, schema v54), so a restart
     does not clear it.

After MAX_FAILURES counted attempts inside WINDOW_SECONDS the approver is LOCKED for
LOCK_SECONDS, and while locked even the CORRECT PIN is refused (otherwise a sweep simply
continues until it hits it).

Keyed by (company_id, approver_uid). A reference that matched nobody (a typo, another company's
person, a made-up name) shares ONE bucket per company (`UNKNOWN_APPROVER`), so guessing at names
cannot grow the table without bound.

The clock is injectable (`now`, epoch seconds) so tests do not sleep.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

MAX_FAILURES = 5
WINDOW_SECONDS = 15 * 60
LOCK_SECONDS = 15 * 60

#: Bucket for a reference that resolved to nobody in the company.
UNKNOWN_APPROVER = '*unknown*'

PIN_ATTEMPTS_DDL = """
CREATE TABLE IF NOT EXISTS pin_approval_attempts (
    company_id   TEXT NOT NULL,
    approver_uid TEXT NOT NULL,
    window_start TEXT,
    failures     INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    PRIMARY KEY (company_id, approver_uid)
)
"""


def ensure_table(conn):
    """Idempotent. The v54 migration creates the table; this makes a database that already ran an
    earlier v54 (before this table existed) and a test-built one work too."""
    conn.execute(PIN_ATTEMPTS_DDL)


def _fmt(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def _parse(text):
    if not text:
        return None
    try:
        return datetime.strptime(text, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def reserve_attempt(conn, company_id, approver_uid, now=None):
    """Count ONE attempt against this approver, atomically, BEFORE the PIN is checked.

    Opens its own `BEGIN IMMEDIATE`, so `conn` must have no transaction open. Returns
    `(allowed, retry_after_s, just_locked)`:
      allowed=False  -> the approver is locked; nothing is counted; do NOT verify a PIN.
      allowed=True   -> the attempt is counted (pessimistically, as a failure); verify the PIN,
                        and on success call `release_attempt`. `just_locked` is True when this very
                        attempt took the count to MAX_FAILURES (the caller audits the lockout)."""
    now = time.time() if now is None else now
    company_id, approver_uid = str(company_id), str(approver_uid)
    conn.execute('BEGIN IMMEDIATE')
    try:
        row = conn.execute(
            'SELECT window_start, failures, locked_until FROM pin_approval_attempts '
            'WHERE company_id=? AND approver_uid=?', (company_id, approver_uid)).fetchone()
        failures, window_start, locked_until = 0, None, None
        if row:
            failures = int(row[1] or 0)
            window_start, locked_until = _parse(row[0]), _parse(row[2])
        if locked_until is not None and locked_until > now:
            conn.execute('COMMIT')
            return False, max(1, int(locked_until - now) + 1), False
        # A lock that has run out, or a window that has run out, starts a clean count.
        if locked_until is not None or window_start is None or now - window_start >= WINDOW_SECONDS:
            failures, window_start, locked_until = 0, now, None
        failures += 1
        just_locked = failures >= MAX_FAILURES
        if just_locked:
            locked_until = now + LOCK_SECONDS
        conn.execute(
            'INSERT INTO pin_approval_attempts (company_id, approver_uid, window_start, failures, '
            'locked_until) VALUES (?,?,?,?,?) ON CONFLICT(company_id, approver_uid) DO UPDATE SET '
            'window_start=excluded.window_start, failures=excluded.failures, '
            'locked_until=excluded.locked_until',
            (company_id, approver_uid, _fmt(window_start), failures,
             _fmt(locked_until) if locked_until is not None else None))
        conn.execute('COMMIT')
        return True, 0, just_locked
    except BaseException:
        try:
            conn.execute('ROLLBACK')
        except Exception:
            pass
        raise


def release_attempt(conn, company_id, approver_uid):
    """Undo the ONE increment `reserve_attempt` made for a PIN that then verified.

    Decrements (floor 0) -- never resets -- so failures that concurrent attempts counted stay
    counted. If that takes the count back under MAX_FAILURES the lock this attempt set (if any)
    is lifted with it. Opens its own `BEGIN IMMEDIATE`."""
    company_id, approver_uid = str(company_id), str(approver_uid)
    conn.execute('BEGIN IMMEDIATE')
    try:
        conn.execute(
            'UPDATE pin_approval_attempts SET failures = MAX(0, failures - 1), '
            'locked_until = CASE WHEN MAX(0, failures - 1) < ? THEN NULL ELSE locked_until END '
            'WHERE company_id=? AND approver_uid=?', (MAX_FAILURES, company_id, approver_uid))
        conn.execute('COMMIT')
    except BaseException:
        try:
            conn.execute('ROLLBACK')
        except Exception:
            pass
        raise
