"""Aura Retail -- W2b/W3a: promotions and extras configuration shared between
computers (schema v50).

WHY THIS MODULE EXISTS (W2b/W3a). Promotions and the extras configuration
(modifier groups, options, and which product carries which group) never left
the computer they were set up on: a promotion made on A was not applied on B
(full price), B's till could not offer A's extras, and a sale with extras or a
promotion made on A showed a plain line in B's receipts, reprints and reports.
(Money was right all along -- prices are baked into sale_items.)

The small rules below live here because the migration (database/schema.py),
the API (api/retail_api.py) and the sync apply (commercial_runtime/sync/
sync_service.py) all need the SAME answer. schema.py and sync_service cannot
all import products code, so each carries a twin of the one literal / one
function it needs and a test pins the twin equal (the W2a / W3b / W4a pattern).

  * `NS_PROMO` / `promotion_legacy_uid` -- a pure function of what a promotion
    IS, so the same promotion typed on two computers before v50 collapses into
    one row when they first sync instead of appearing twice (and applying twice).
  * `lww_incoming_wins` -- the ONE merge rule: last writer wins per row on
    (updated_at_utc, then row_version, then the payload's content as a final,
    deterministic tiebreak), so both computers pick the SAME winner no matter
    which order the two edits arrive in. A pure function, no I/O.
  * `next_stamp` / `parse_stamp` -- every configuration writer stamps its edit
    with a time strictly after the version it is editing (F1, see below).
  * `config_values_differ` -- whether two versions of a row disagree on any
    field both sides carry; a conflict is worth recording only when they do.

NO IMPORT OF api/ OR general_ledger: nothing here queues or writes anything.
"""
from __future__ import annotations

import datetime as _dt
import json
import uuid
from decimal import Decimal

#: NEVER CHANGE. Two computers must derive the SAME uid for the same legacy
#: promotion; a different namespace on one of them would make each mint its own
#: uid, the unique index would not collide, and the shop would carry the same
#: promotion twice -- silently, because nothing errors. schema.py carries a
#: twin and a test pins the two equal.
NS_PROMO = uuid.UUID('6f2c8a41-d3b7-4e95-a108-5c9e7b3d2f64')

#: Keys of a row/payload that are bookkeeping, not content. They are never
#: compared by the tiebreak: `company_id` is restamped by each receiver, the
#: version/time pair is compared on its own earlier in the rule, and
#: `_changed_fields` / `sync_backfill_pending` / `id` describe the transport or
#: the local row, not the shop's configuration.
_META_KEYS = frozenset({
    '_changed_fields', 'company_id', 'row_version', 'updated_at_utc',
    'sync_backfill_pending', 'id',
})


def canonical_number(value) -> str:
    """A number as one canonical decimal string ('10', never '10.0' or
    '1E+1'), so the same percentage spelled two ways derives the same uid."""
    text = format(Decimal(str(value)).normalize(), 'f')
    return '0' if text in ('-0', '') else text


def _canon(value):
    """JSON-ready, spelling-independent form of one field value: a whole float
    is the int it equals (10.0 == 10), other floats are rounded to nine places
    (float noise is not a difference), everything else is itself."""
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else round(value, 9)
    if isinstance(value, dict):
        return {str(k): _canon(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canon(v) for v in value]
    return str(value)


def canonical_json(obj) -> str:
    """Deterministic JSON text of `obj`: sorted keys, no whitespace, no ASCII
    escaping, numbers in canonical form."""
    return json.dumps(_canon(obj), sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False)


def promotion_legacy_uid(name, discount_pct, product_id, category_id,
                         starts_at, ends_at, branch_uid, ordinal=0) -> str:
    """The uid of a promotion that existed before v50 -- the same on every
    computer for the same promotion.

    Derived from what the promotion IS: its name, percentage, target
    (product / category), validity window and branch (by the branch's UID,
    '' for all branches -- a local branch id means nothing on another computer).
    `ordinal` separates promotions on ONE computer that are field-for-field
    identical (0 for the first by id, 1 for the next, ...): without it the
    second would collide with the first on the unique uid index. Ordinal 0
    adds nothing to the key, so a lone promotion's uid does not depend on it,
    and two computers each holding the same N identical copies derive the same
    N uids and still collapse pairwise."""
    parts = [
        str(name if name is not None else ''),
        canonical_number(discount_pct if discount_pct is not None else 0),
        '' if product_id is None else str(product_id),
        '' if category_id is None else str(category_id),
        '' if starts_at is None else str(starts_at),
        '' if ends_at is None else str(ends_at),
        branch_uid or '',
    ]
    if ordinal:
        parts.append('#%d' % int(ordinal))
    return str(uuid.uuid5(NS_PROMO, canonical_json(parts)))


def _version(row) -> int:
    value = row.get('row_version')
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


_UTC = _dt.timezone.utc
_STAMP_FORMAT = '%Y-%m-%dT%H:%M:%S.%fZ'


def parse_stamp(value):
    """A stored `updated_at_utc` as an aware UTC datetime, or None.

    Accepts every spelling this suite has written: 'YYYY-MM-DDTHH:MM:SS' with an
    optional fraction and an optional 'Z' / '+00:00' (what `now_utc_iso` and
    `next_stamp` write) and the space-separated 'YYYY-MM-DD HH:MM:SS' (SQLite's
    own clock). A naive value is read as UTC. None, a non-string or anything
    unreadable is None -- which the merge rule treats as the OLDEST possible
    stamp, so a garbled value can never beat a real one and never raises."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace(' ', 'T', 1)
    if text.endswith(('Z', 'z')):
        text = text[:-1] + '+00:00'
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=_UTC)
    return parsed.astimezone(_UTC)


def next_stamp(prev=None, now=None) -> str:
    """The `updated_at_utc` for an edit made on top of a row stamped `prev`.

    F1 (fix round 1). The merge rule compares STAMPS first, so an edit must
    carry a stamp strictly later than the version it edits -- otherwise a
    computer whose clock runs a little BEHIND the one that wrote `prev` would
    stamp its edit earlier than the thing it edited, and every other computer
    would keep the old version. So: the clock's time, unless that is not after
    `prev`, in which case `prev` plus one microsecond. A first write (no/unreadable
    `prev`) is just the clock. `now` is injectable for tests only.

    RESIDUAL, accepted and written down: between two CONCURRENT edits (neither
    made on top of the other) the later CLOCK wins, so a computer whose clock is
    far AHEAD wins every clash with the others. It is never silent -- the losing
    edit is always recorded for a person to look at (config_edit_superseded)."""
    clock = (now or _dt.datetime.now(_UTC)).astimezone(_UTC)
    before = parse_stamp(prev)
    if before is not None and clock <= before:
        clock = before + _dt.timedelta(microseconds=1)
    return clock.strftime(_STAMP_FORMAT)


def _content(row, keys) -> str:
    return canonical_json({k: row.get(k) for k in keys})


def _shared_keys(local, incoming):
    return sorted((set(local) & set(incoming)) - _META_KEYS)


def config_values_differ(local, incoming) -> bool:
    """True when the two versions disagree on any content field BOTH carry."""
    keys = _shared_keys(local, incoming)
    return _content(local, keys) != _content(incoming, keys)


_OLDEST = _dt.datetime.min.replace(tzinfo=_UTC)


def lww_incoming_wins(local, incoming) -> bool:
    """Does the incoming version of a configuration row replace the local one?

    Last writer wins, per row, on (updated_at_utc, row_version, content):
      1. a different STAMP decides: the later one wins. A missing or unreadable
         stamp is the oldest possible. (F1: the stamp used to be compared
         AFTER row_version, so a computer that had edited a promotion three
         times -- rv 4, older stamps -- beat a later deactivation at rv 2, and
         the promotion came back ON everywhere and kept discounting.)
      2. equal stamps: the higher row_version wins;
      3. both equal: the greater canonical JSON of the shared content fields
         wins. This last step is arbitrary but DETERMINISTIC -- computer A
         comparing (its row, B's row) and computer B comparing (its row, A's
         row) reach opposite verdicts on the same pair, so exactly one of them
         keeps its row and they converge. Identical content is no win at all
         (nothing to write).
    The twin in sync_service.py is pinned equal by a test. `local` and
    `incoming` are plain dicts (a DB row / a wire payload). Never raises on
    missing keys: a missing version is 0, a missing stamp is the oldest."""
    s_in = parse_stamp(incoming.get('updated_at_utc')) or _OLDEST
    s_local = parse_stamp(local.get('updated_at_utc')) or _OLDEST
    if s_in != s_local:
        return s_in > s_local
    rv_in, rv_local = _version(incoming), _version(local)
    if rv_in != rv_local:
        return rv_in > rv_local
    keys = _shared_keys(local, incoming)
    return _content(incoming, keys) > _content(local, keys)
