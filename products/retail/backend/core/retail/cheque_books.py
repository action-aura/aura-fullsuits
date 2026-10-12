"""
Aura Retail -- MONEY-3 M4: a cheque's journals follow the MERGED history.

`cheques.desired_postings(events)` is the pure statement of which journals the general ledger
should hold for one cheque, derived from its full event set. THIS module is the idempotent half
that makes the ledger say it: `reconcile_cheque_journals`. It lives in core/ (raw sqlite3 and the
ledger helpers, no Flask) rather than in api/retail_api.py because the v51 migration
(database/schema.py) must call it too, and database/ may not import api/.

WHY THE LEDGER NEEDS A RECONCILER AT ALL. Journals are append-only (a trigger forbids UPDATE and
DELETE), and each computer used to post them from its OWN local fold at the moment it wrote an
event. Two computers that each did one legal thing offline (A bounces, B clears) each posted a
journal the merged history contradicts. The only sanctioned correction of a posted journal is a
reversing journal, so convergence here means: post what the history wants and is missing, and
reverse what the history no longer wants.

THE TWO MOVES, and why they converge on every computer.

  * A wanted journal that is absent is posted under its deterministic uid
    (`general_ledger.cheque_journal_uid`: a pure function of cheque + kind + index). Two computers
    that post it independently mint the same uid and the sync apply collapses them.
  * An unwanted journal that is still net-live is reversed; a wanted journal that is net-reversed
    is "un-reversed" by reversing its last reversal. Both are `reverse_journal` on the LAST LINK
    of the journal's reversal chain (original -> its reversal -> the reversal of that reversal ...):
    a journal is reversed at most once and the reversal's uid is a pure function of the reversed
    journal's uid (`general_ledger.reversal_uid`), so two computers that each extend the chain by
    one link mint the SAME link, and a chain of even length (0, 2, ...) is net-live while an odd
    one is net-reversed.

Churn is allowed and bounded: a computer that sees a journal BEFORE the event that justifies it
(the relay does not order across entity types) may reverse it and later un-reverse it. The NET is
right at every step the event set is complete, and the chain never grows once the sets agree,
because a link is only added when the net state disagrees with the wanted state.

Never commits; the caller owns the transaction.
"""
from datetime import datetime, timezone

from core.retail import cheques as cheque_engine
from core.retail import general_ledger as gl
from core.retail import metrics


def event_business_date(created_at_utc, conn=None, cid=None):
    """The local calendar date of an event's UTC timestamp -- the same clock `general_ledger._today()`
    reads (the device's local zone), applied to the EVENT's instant instead of to "now". A journal
    is dated by the day the bank fact happened, not by the day a computer got around to booking
    it: a cheque cleared 10 days before an upgrade, or on a peer that synced yesterday, must land
    in the cash flow of the day it cleared. Unparseable or missing -> today (never raises).

    MONEY-4a / D1: with `conn` and `cid` the instant is moved onto the shop's BUSINESS-day clock
    (`metrics.business_now`) before the date is taken, so a cheque cleared at 01:30 in a shop whose
    trading day starts at 06:00 lands on the day the sales reports file that moment under. Without
    them (or with start hour 0 and no declared zone) it is the plain local calendar date."""
    try:
        parsed = datetime.fromisoformat(str(created_at_utc).replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if conn is not None and cid is not None:
            return metrics.business_now(conn, cid, parsed).strftime('%Y-%m-%d')
        return parsed.astimezone().strftime('%Y-%m-%d')
    except (TypeError, ValueError):
        return gl._today()


def _chain_end(conn, cid, journal_id):
    """(id of the last link, number of reversal links) of the reversal chain that starts at
    `journal_id`. Even length = the journal is net-live; odd = net-reversed."""
    length = 0
    current = journal_id
    while True:
        nxt = conn.execute(
            "SELECT id FROM gl_journals WHERE company_id=? AND reversed_journal_id=? "
            "ORDER BY id LIMIT 1", (cid, current)).fetchone()
        if nxt is None:
            return current, length
        current = nxt['id']
        length += 1


def _memo_for(kind, event_type, direction, number):
    if kind == 'apply':
        return f"Cheque {number} {'received' if direction == 'in' else 'issued'}"
    return f"Cheque {number} {event_type}"


def reconcile_cheque_journals(conn, cid, cheque, events):
    """Make the general ledger hold exactly the journals `desired_postings(events)` names for
    `cheque` (a row/mapping with id, branch_id, cheque_number, direction, currency, amount).
    Idempotent: calling it again with the same event set posts nothing. Returns the number of
    journals posted (new postings plus reversals).

    Reversals are dated by the latest event of the cheque (the moment the history changed its
    mind), new postings by the event that drives them."""
    cheque_id = cheque['id']
    number = cheque['cheque_number']
    direction = cheque['direction']
    by_id = {e['id']: e for e in events}
    postings = cheque_engine.desired_postings(events)
    wanted = {gl.cheque_journal_uid(cheque_id, p.kind, p.index): p for p in postings}
    latest = max((e['created_at_utc'] or '' for e in events), default='')
    reversal_date = event_business_date(latest, conn, cid) if latest else gl._today()

    existing = {row['uid']: row['id'] for row in conn.execute(
        "SELECT id, uid FROM gl_journals WHERE company_id=? AND source_type='cheque' AND source_uid=?",
        (cid, cheque_id)).fetchall()}

    changed = 0
    for uid, posting in wanted.items():
        driving = by_id.get(posting.event_id)
        if uid not in existing:
            gl.post_cheque_journal(
                conn, cid, cheque['branch_id'], cheque_id, number, direction, posting.kind, posting.index,
                cheque['currency'] or 'JOD', cheque['amount'],
                _memo_for(posting.kind, driving['event_type'] if driving else posting.kind, direction, number),
                business_date=event_business_date(driving['created_at_utc'], conn, cid) if driving else None)
            changed += 1
            continue
        last, length = _chain_end(conn, cid, existing[uid])
        if length % 2 == 1:
            # wanted again after having been reversed: reverse the reversal
            gl.reverse_journal(conn, cid, last, memo=f"Cheque {number} re-instated by the merged history",
                               business_date=reversal_date)
            changed += 1
    for uid, journal_id in existing.items():
        if uid in wanted:
            continue
        last, length = _chain_end(conn, cid, journal_id)
        if length % 2 == 0:
            gl.reverse_journal(conn, cid, last, memo=f"Cheque {number} withdrawn by the merged history",
                               business_date=reversal_date)
            changed += 1
    return changed
