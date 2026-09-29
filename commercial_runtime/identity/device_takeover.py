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
Nothing else in the identity package imports this module, no schema was added
for it, and no sync payload carries a "takeover" flag: a takeover is an
ordinary epoch+1 `admin_device` assignment (see below), so peers cannot tell
it from a hand-over and nothing on the wire has to be undone.

WHY IT EXISTS
The main computer is the only one allowed to hand the role over
(device_routes.transfer_admin_device_route). If that computer is lost, burnt or
stolen, nobody can ever hand it over, and a shop is stuck with no main computer
for good. This is the escape hatch, and because it lets ONE till take the role
away from the real main, it is deliberately hard to trigger by accident or by a
cashier who found an unlocked till:

  - the signed-in user is a company admin (checked by the route);
  - the admin types their PASSWORD again (the route reuses the login check, so
    the lockout/throttle that protects login protects this too);
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
from datetime import datetime, timezone

from commercial_runtime.identity import device_registry

TAKEOVER_MIN_UNSEEN_DAYS = 7
TAKEOVER_CONFIRM_PHRASE = 'TAKE OVER'

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
    the answer errs toward "seen recently" (refuse). A main whose device row
    never arrived here, or whose every timestamp is unreadable, is None --
    and None refuses (fail closed): we do not take a role over from a
    computer we know nothing about."""
    assignment = device_registry.get_assignment(conn, company_id)
    if assignment is None:
        return None
    stamps = [assignment.get('assigned_at_utc')]
    row = device_registry.get_device(conn, assignment['device_id'])
    if row is None or row.get('company_id') != company_id:
        return None
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
    heard = main_last_heard_from(conn, company_id)
    if heard is None:
        raise TakeoverRefused('TAKEOVER_MAIN_UNKNOWN', TAKEOVER_UNKNOWN_MESSAGE)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
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
    `admin_device` event, one commit. Returns the new device row.

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
        by_user_uid=by_user_uid, by_device_id=this_device_id)
