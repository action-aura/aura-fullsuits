"""GUARDED LOCAL TAKEOVER of the main-computer role (Registry v9, plan option (b)).

*** THIS WHOLE FILE IS ONE REMOVABLE UNIT. ***
The owner may instead choose option (a): "recovery is done from the Owner
Control Center, never from a till". If (a) is chosen, remove this unit by
deleting exactly four things and nothing else:
  1. this file,
  2. the block in device_routes.py between the lines
       "# ==== LOCAL TAKEOVER (option b) -- BEGIN ===="  and  "... END ====",
  3. tests/test_device_takeover.py,
  4. the takeover strings in tmp/p2s/i18n-R.json (the ones mentioning "take over").
Nothing else in the identity package imports this module. Since the
2026-09-29 security review a takeover is NOT invisible any more, so option (a)
removal must also undo what the review added (each is small and guarded):
  5. device_sync_schema.py: the `takeover_lockout` table and the
     `admin_device_assignment.takeover` column (both additive; leaving them in
     place on an existing registry.db is harmless),
  6. device_registry.py: the `takeover` key in `admin_device_sync_payload`,
     the `takeover=` argument of `_write_assignment` /
     `transfer_admin_device_with_assignment`, and the `MAIN_TAKEN_OVER` audit
     row written by `apply_admin_assignment` / `record_main_taken_over`.
A takeover is still an epoch+1 `admin_device` assignment, but it now carries
`takeover=true` so every receiver writes a WARNING audit row naming the old and
the new main: the owner can no longer be surprised by a role change nobody
recorded.

WHY IT EXISTS
The main computer is the only one allowed to hand the role over
(device_routes.transfer_admin_device_route). If that computer is lost, burnt or
stolen, nobody can ever hand it over, and a shop is stuck with no main computer
for good. This is the escape hatch, and because it lets ONE till take the role
away from the real main, it is deliberately hard to trigger by accident or by a
cashier who found an unlocked till:

  - the signed-in user is a company admin (checked by the route);
  - the admin types their PASSWORD again. The check is NON-COUNTING against
    login (a wrong guess here must not lock the owner out of signing in) and has
    its OWN lockout instead: 3 misses, then 15 minutes, doubling on each
    further miss (see takeover_lock_state / record_takeover_failure);
  - the CURRENT main computer has not been heard from for at least
    TAKEOVER_MIN_UNSEEN_DAYS days -- a live main is never overridden from a
    till;
  - the typed confirmation phrase is exactly TAKEOVER_CONFIRM_PHRASE;
  - the assignment epoch goes UP by one. That is the whole recovery story if the
    old main was only switched off for a week: when it comes back and syncs it
    receives a HIGHER epoch than the one it holds, loses
    (`device_registry.admin_assignment_wins`), and drops its main flag by itself.
    No merge, no manual cleanup.

It only ever acts on THIS computer (the route has no target parameter): nobody
can use it to push the role onto some other machine.

RESIDUAL RISK, stated plainly: "not heard from for 7 days" is judged from
this computer's copy of the main's `last_seen_at`. A computer that has itself
been cut off from sync for longer than 7 days sees a stale value and could
believe the main is dead when it is not. The password, the typed phrase and the
epoch rule (the real main wins back nothing, but the owner can hand it over
again) bound the damage; they do not remove it. Owner-side reset (option a) does.

Stdlib only, no Flask: this module stays Android-safe like device_registry.
"""
from datetime import datetime, timedelta, timezone

from commercial_runtime.identity import device_registry

TAKEOVER_MIN_UNSEEN_DAYS = 7
TAKEOVER_CONFIRM_PHRASE = 'TAKE OVER'

# Password-guess back-off for the takeover prompt (2026-09-29 review, M2).
TAKEOVER_FREE_ATTEMPTS = 3
TAKEOVER_BASE_LOCK_SECONDS = 15 * 60
# Doubling stops here (15 min * 2**10 is ~10.7 days): bounded arithmetic, and a
# lock longer than the 7-day silence window would just be a permanent lock.
TAKEOVER_MAX_LOCK_DOUBLINGS = 10

# Fixed literals (the frontend looks whole sentences up in the locale files).
TAKEOVER_CONFIRM_MESSAGE = 'Type TAKE OVER to confirm.'
TAKEOVER_PASSWORD_MESSAGE = 'Your password is not correct.'
TAKEOVER_NO_MAIN_MESSAGE = (
    'This shop has no main computer to replace. Set this computer as the main computer instead.'
)
TAKEOVER_ALREADY_MAIN_MESSAGE = 'This computer is already the main computer.'
TAKEOVER_SEEN_RECENTLY_MESSAGE = (
    'The main computer was online in the last 7 days. You can only take over '
    'when it has been silent for at least 7 days.'
)
TAKEOVER_UNKNOWN_MESSAGE = (
    'This computer cannot tell when the main computer was last online, so it cannot take over.'
)
TAKEOVER_NOT_ACTIVE_MESSAGE = 'This computer was removed from the shop and cannot take over.'
TAKEOVER_CLOCK_BEHIND_MESSAGE = (
    'The clock on this computer is behind the shop records. Correct the date and '
    'time, then try again.'
)
TAKEOVER_LOCKED_MESSAGE = 'Too many wrong passwords. Try again later.'
# Refuse if this computer's clock is earlier than the newest time already
# recorded for any computer of the shop by more than this much.
TAKEOVER_CLOCK_SLACK_SECONDS = device_registry.FUTURE_SKEW_SECONDS


class TakeoverRefused(Exception):
    """A takeover was refused. `.code` is machine-readable, `.message` a fixed
    plain-words literal that is safe to show the user."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def confirm_phrase_ok(text) -> bool:
    """The typed confirmation. Exact (after trimming the ends): a near-miss
    must not count, the point is that the person meant it."""
    return isinstance(text, str) and text.strip() == TAKEOVER_CONFIRM_PHRASE


def main_last_heard_from(conn, company_id: str):
    """The most recent moment this computer has evidence the CURRENT main was
    alive, as an aware datetime, or None if it cannot tell.

    Takes the LATEST of last_seen_at / updated_at_utc / first_seen_at of the
    main's device row and of the assignment itself: any sign of life counts, so
    the answer errs toward "seen recently" (refuse).

    Two cases count ONLY the assignment time (2026-09-29 review):
      - the main's device row is missing here (a "ghost" main: an assignment
        naming a computer that never announced itself to this one). It used to
        answer None, and None refuses -- forever, so one forged or orphaned
        assignment made the shop unrecoverable. The assignment time is the one
        piece of evidence that exists, so silence is measured from it: after 7
        days a takeover is allowed. A CLAIM stays refused (claim_admin_device_
        with_assignment is untouched);
      - the main's row is REVOKED. A removed computer's later timestamps are not
        a sign the main is alive (a revoke of the main is refused everywhere
        now, so this is a row that arrived out of order or from a forger).
    Only an assignment time that cannot be read at all is None -- and None
    still refuses (fail closed)."""
    assignment = device_registry.get_assignment(conn, company_id)
    if assignment is None:
        return None
    stamps = [assignment.get('assigned_at_utc')]
    row = device_registry.get_device(conn, assignment['device_id'])
    row_counts = (row is not None and row.get('company_id') == company_id
                  and (row.get('status') or '').lower() != 'revoked')
    if row_counts:
        stamps += [row.get('last_seen_at'), row.get('updated_at_utc'), row.get('first_seen_at')]
    parsed = [p for p in (device_registry._parse_utc(s) for s in stamps) if p is not None]
    return max(parsed) if parsed else None


def newest_recorded_time(conn, company_id: str):
    """The newest timestamp this computer holds for ANY device of the company or
    for the assignment, as an aware datetime, or None when there is none."""
    stamps = []
    assignment = device_registry.get_assignment(conn, company_id)
    if assignment is not None:
        stamps.append(assignment.get('assigned_at_utc'))
    for row in device_registry.list_devices(conn, company_id):
        stamps += [row.get('last_seen_at'), row.get('updated_at_utc'), row.get('first_seen_at')]
    parsed = [p for p in (device_registry._parse_utc(s) for s in stamps) if p is not None]
    return max(parsed) if parsed else None


def check_takeover_allowed(conn, company_id: str, this_device_id: str, *, now=None) -> dict:
    """Raise TakeoverRefused unless `this_device_id` may take the role over
    now. Returns the current assignment on success. Reads only.

    The order matters only for which message a person sees; every branch is a
    refusal, and the function has exactly one way to say yes."""
    me = device_registry.get_device(conn, this_device_id)
    if (me is None or me.get('company_id') != company_id
            or (me.get('status') or '').lower() != 'active'):
        raise TakeoverRefused('TAKEOVER_DEVICE_NOT_ACTIVE', TAKEOVER_NOT_ACTIVE_MESSAGE)
    assignment = device_registry.get_assignment(conn, company_id)
    if assignment is None:
        # Nobody was ever main: the ordinary first claim is the right door.
        raise TakeoverRefused('TAKEOVER_NO_MAIN', TAKEOVER_NO_MAIN_MESSAGE)
    if assignment['device_id'] == this_device_id:
        raise TakeoverRefused('TAKEOVER_ALREADY_MAIN', TAKEOVER_ALREADY_MAIN_MESSAGE)
    now = _aware(now)
    # M1 (2026-09-29): "silent for 7 days" is measured against THIS computer's
    # clock. If the newest thing already recorded for the shop lies in this
    # computer's future, the clock has been set back (or has not been fixed
    # after a reset), records look older than they are, and it is not to be
    # trusted with a decision like this.
    newest = newest_recorded_time(conn, company_id)
    if newest is not None and now < newest - timedelta(seconds=TAKEOVER_CLOCK_SLACK_SECONDS):
        raise TakeoverRefused('TAKEOVER_CLOCK_BEHIND', TAKEOVER_CLOCK_BEHIND_MESSAGE)
    heard = main_last_heard_from(conn, company_id)
    if heard is None:
        raise TakeoverRefused('TAKEOVER_MAIN_UNKNOWN', TAKEOVER_UNKNOWN_MESSAGE)
    # (now - heard) may be NEGATIVE when the main's clock ran ahead of ours; a
    # negative gap is "seen just now", so it refuses like any recent sighting.
    silent_days = (now - heard).total_seconds() / 86400.0
    if silent_days < TAKEOVER_MIN_UNSEEN_DAYS:
        raise TakeoverRefused('TAKEOVER_MAIN_SEEN_RECENTLY', TAKEOVER_SEEN_RECENTLY_MESSAGE)
    return assignment


def take_over_main(conn, company_id: str, this_device_id: str, *,
                   by_user_uid: str = None, now=None) -> dict:
    """Make THIS computer the main computer. Re-checks everything inside the
    write transaction (BEGIN IMMEDIATE, so a peer's assignment event applied a
    moment ago cannot slip between the check and the write), then reuses the
    ordinary hand-over write: epoch+1, the flag moves clear-then-set, one
    `admin_device` event marked `takeover=true` (so every receiver records a
    WARNING audit row), one commit. Returns the new device row.

    Raises TakeoverRefused; nothing is written in that case."""
    if not conn.in_transaction:
        conn.execute('BEGIN IMMEDIATE')
    try:
        check_takeover_allowed(conn, company_id, this_device_id, now=now)
    except TakeoverRefused:
        conn.rollback()
        raise
    return device_registry.transfer_admin_device_with_assignment(
        conn, company_id, this_device_id,
        by_user_uid=by_user_uid, by_device_id=this_device_id, takeover=True)


# -- the takeover's OWN password back-off (2026-09-29 review, M2) --------------

def _aware(now=None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now


def password_matches(conn, user_id, password) -> bool:
    """True when `password` is the stored password of `user_id`. NON-COUNTING:
    it never touches users.failed_login_count / locked_until, so a wrong guess
    at the takeover prompt cannot lock the owner out of signing in (it used to:
    the route called the login check itself). The caller keeps the takeover's
    own counter (record_takeover_failure)."""
    if not user_id or not isinstance(password, str) or not password:
        return False
    row = conn.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None:
        return False
    from commercial_runtime.security.passwords import authenticate_and_maybe_upgrade
    ok, _ = authenticate_and_maybe_upgrade(password, row[0] or '')
    return bool(ok)


def takeover_lock_state(conn, user_id, device_id, *, now=None):
    """(locked, retry_after_seconds) for this user on this computer. Read only.
    Check it BEFORE looking at the password, so a locked user cannot keep
    guessing (a right-or-wrong answer while locked must reveal nothing)."""
    now = _aware(now)
    row = conn.execute(
        "SELECT locked_until_utc FROM takeover_lockout WHERE user_id=? AND device_id=?",
        (user_id, device_id)).fetchone()
    until = device_registry._parse_utc(row[0]) if row and row[0] else None
    if until is None or until <= now:
        return False, 0
    return True, int((until - now).total_seconds()) + 1


def record_takeover_failure(conn, user_id, device_id, *, now=None) -> int:
    """Count one wrong takeover password; commits. Returns the lock length in
    seconds that this miss started (0 while still inside the free attempts).
    The 3rd miss locks for 15 minutes, the 4th for 30, the 5th for 60, ...
    doubling, capped at TAKEOVER_MAX_LOCK_DOUBLINGS doublings."""
    now = _aware(now)
    row = conn.execute(
        "SELECT failed_count FROM takeover_lockout WHERE user_id=? AND device_id=?",
        (user_id, device_id)).fetchone()
    failed = (int(row[0] or 0) if row else 0) + 1
    lock_seconds = 0
    locked_until = None
    if failed >= TAKEOVER_FREE_ATTEMPTS:
        doublings = min(failed - TAKEOVER_FREE_ATTEMPTS, TAKEOVER_MAX_LOCK_DOUBLINGS)
        lock_seconds = TAKEOVER_BASE_LOCK_SECONDS * (2 ** doublings)
        locked_until = (now + timedelta(seconds=lock_seconds)).isoformat()
    conn.execute(
        "INSERT INTO takeover_lockout (user_id, device_id, failed_count, locked_until_utc, last_failed_utc) "
        "VALUES (?,?,?,?,?) ON CONFLICT(user_id, device_id) DO UPDATE SET "
        "failed_count=excluded.failed_count, locked_until_utc=excluded.locked_until_utc, "
        "last_failed_utc=excluded.last_failed_utc",
        (user_id, device_id, failed, locked_until, now.isoformat()))
    conn.commit()
    return lock_seconds


def clear_takeover_failures(conn, user_id, device_id) -> None:
    """A correct password resets the takeover counter; commits."""
    conn.execute("DELETE FROM takeover_lockout WHERE user_id=? AND device_id=?",
                 (user_id, device_id))
    conn.commit()
