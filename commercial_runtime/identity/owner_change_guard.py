"""AUDIT-033 (S-AUTH, D5): the owner's own sign-in is changed at the main
computer only.

WHY. Every other computer in the shop accepts a signed `user` event about the
owner (password_hash, pin_hash, status, permissions...) only from the main
computer (the receiver's `owner_change_needs_main` rule). A change made on a
non-main till would therefore apply locally, be parked everywhere else, and
leave the owner with different passwords on different computers. Refusing it at
the source turns a silent split into a plain sentence the person can act on.

RULE. Refuse only when BOTH hold: this computer is NOT the main computer AND a
main computer IS assigned for the company. A fresh single install has no
assignment (nothing to be "not main" of), so it keeps working exactly as
before -- the "invisible unless opted in" rule of this codebase.

FAILURE MODES, decided on purpose:
  * assignment readable + local device state corrupt -> "not main" (refuse):
    an unreadable local identity must not be able to talk its way into owner
    edits (same fail-closed stance as retail's `_local_device_is_main`).
  * the registry cannot be read at all -> allow, and log. We cannot know there
    is a main; the receiving side still enforces the same rule, so nothing
    unauthorised can spread.
Display name and language are NOT guarded (callers simply do not call this).
"""
import logging

from flask import jsonify

from commercial_runtime.identity import device_context as _device_context
from commercial_runtime.identity import device_registry as _device_registry
from commercial_runtime.identity import user_accounts as _accounts

log = logging.getLogger(__name__)

OWNER_CHANGE_NEEDS_MAIN_COMPUTER = 'OWNER_CHANGE_NEEDS_MAIN_COMPUTER'
# Whole-sentence catalog key (en.json / ar.json) -- the screens translate by
# looking this exact sentence up, the same mechanism as every other refusal.
OWNER_CHANGE_NEEDS_MAIN_MESSAGE = "The owner's own sign-in can only be changed at the main computer."


def _blocked_here(conn, company_id) -> bool:
    try:
        assignment = _device_registry.get_assignment(conn, company_id)
    except Exception:
        log.warning('owner change guard: could not read the main-computer assignment; allowed',
                    exc_info=True)
        return False
    if assignment is None:
        return False
    try:
        return not _device_context.local_device_is_admin(conn, company_id)
    except Exception:
        # A main is assigned and this computer cannot prove it is that one.
        return True


def owner_row_version_frozen_here(conn, company_id, target_role) -> bool:
    """True when a cosmetic edit of the OWNER's row (display name, language) made
    on THIS computer must NOT move that row's row_version / updated_at_utc.

    AUDIT-033 fix round (verifier V finding #3). A non-main computer may still
    rename the owner or change the owner's language, and the event it queues
    reaches every receiver. But receivers decide whether a `user` event beats
    their copy by row_version, and a non-main computer's harmless edit used to
    bump the OWNER row to local+1, so a hostile (or merely busy) computer could
    push the owner row's version arbitrarily high with "harmless" edits and
    make every later main-signed owner change lose the version race. The
    receiver (sync_service, harmless-owner-edit path) therefore writes ONLY
    display_name and language for such an event and never moves the version.

    This is the SENDER's matching half: if this computer bumped its own copy to
    local+1 while the receivers kept theirs, the main's next change (also
    local+1, from the same base) would tie with this computer's own row and be
    ignored here. So the version of the owner row is moved by main-signed
    changes only: it stays frozen exactly where the receiver's rule does.

    Same condition as owner_change_refusal (an owner row, a main assigned, this
    computer is not it); the same fail-closed/fail-open choices apply. A shop
    with no main assigned, the main itself, and every ordinary user are
    unaffected and bump as they always did.
    """
    if _accounts.normalize_role(target_role) != _accounts.ROLE_ADMIN:
        return False
    return _blocked_here(conn, company_id)


def owner_change_refusal(conn, company_id, target_role):
    """None when the change may go ahead, else a ready (response, 409) tuple.

    `target_role` is the raw `users.role` of the row about to change; anything
    that is not the owner is never refused here."""
    if _accounts.normalize_role(target_role) != _accounts.ROLE_ADMIN:
        return None
    if not _blocked_here(conn, company_id):
        return None
    return jsonify({
        'status': 'error',
        'code': OWNER_CHANGE_NEEDS_MAIN_COMPUTER,
        'error': OWNER_CHANGE_NEEDS_MAIN_MESSAGE,
        'message': OWNER_CHANGE_NEEDS_MAIN_MESSAGE,
    }), 409
