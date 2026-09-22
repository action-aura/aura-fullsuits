"""JoFotara e-invoicing -- per-company settings and the enabled/disabled
resolution.

Four independent disable layers, evaluated most-forceful first (each one
alone is sufficient to fully disable the feature, matching
docs/einvoicing/phase1/operational-runbook-and-kill-switch.md's rollback
ladder):

  1. AURA_EINVOICING_DISABLED=1  -- build/ops hard off, no DB or file I/O.
  2. killswitch.py's DISABLED flag file -- fastest per-install override.
  3. This company's `tax_regime` setting in einvoice_settings -- WHICH TAX
     AUTHORITY, IF ANY, THIS SHOP FILES WITH, and -- since 2026-09-22 -- THE
     LAYER THAT ACTUALLY DECIDES OPT-IN/OPT-OUT (see DEFAULTS['tax_regime']'s
     own comment for the full history: it used to default to 'jordan' and now
     defaults to 'none'). Layers 1, 2 and 4 are all off switches someone must
     know to throw; this one is a statement of fact about the shop. Checked
     BEFORE layer 4 because `enabled` only means something inside a regime.
     Any value that is not exactly 'jordan' resolves the whole feature off --
     covering both the explicit 'none' and an unrecognised/corrupt stored
     value, in the same fail-closed direction layer 4 already uses. Default
     'none', so a fresh install files with nobody until an operator writes
     'jordan' explicitly -- or, for an install that was already filing before
     this default flipped, until schema.py's one-time preservation migration
     writes that row for it automatically (see apply_einvoicing_schema()).
  4. This company's own `enabled` setting in einvoice_settings -- the normal
     per-company opt-in WITHIN a regime; it is meaningless outside one
     (is_enabled() below does not even read it when layer 3 has already
     resolved OFF). Stays '1' by default regardless -- see
     DEFAULTS['enabled']'s own comment for why. "On" here does NOT mean
     documents get submitted or clearance gets claimed -- with no provider
     configured (the shipped default; see providers/unconfigured.py), the
     worker enqueues and holds every document, waiting for the shop to
     complete JoFotara portal registration. Layers 1-3 above are each still
     independently sufficient to turn the whole feature off, including
     against this default.

Settings live in the einvoice_settings table (see
commercial_runtime/einvoicing/schema.py) -- a dedicated key/value store, not
Retail's retail_settings (which Clinic doesn't have at all), so the whole
feature's state is one set of tables the kill switch can reason about
without touching product settings.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from . import killswitch

DEFAULTS = {
    # `enabled` is meaningless OUTSIDE a regime -- is_enabled() below does not
    # even read this key once `tax_regime` (right below) has already resolved
    # the feature off (see the module docstring's layer 3/4 ordering). So the
    # regime is now the ONE place that expresses "off by default"
    # (DEFAULTS['tax_regime']='none'), and this stays '1' deliberately: a shop
    # that DOES set tax_regime='jordan' should not also have to remember to
    # flip a second, independent toggle just to be filing. Two switches that
    # both have to be right is exactly how they drift apart -- this repo has
    # paid for that shape of bug before (CLAUDE.md's round-4 correction: ~25
    # money gates that all had to move together, "never make one of these
    # finer on its own"; same failure mode, two things meant to agree,
    # changed one at a time). Leaving this at '1' means the ONLY thing that
    # changed the day the default flipped is the regime a fresh install
    # starts with -- not a second variable to reason about.
    'enabled': '1',
    # AUDIT: which tax authority, if any, this shop actually files with.
    #
    # Added because DEFAULTS['enabled']='1' directly above was justified by a
    # Jordan-ONLY fact (the ISTD mandate of 2024-05-31, spelled out in this
    # module's docstring) while the default it justifies applies to every
    # install of both products. A UAE, Saudi or Palestinian shop installing
    # Aura today inherits that Jordanian reasoning and silently starts queuing
    # JoFotara outbox rows for a tax authority that has no claim on it. The
    # three pre-existing disable layers are all OFF SWITCHES SOMEONE MUST KNOW
    # TO THROW -- so the defect is the DEFAULT being wrong outside Jordan, not
    # a missing fourth switch, and the fix is a regime the shop can STATE, not
    # another toggle nobody outside Jordan knows to go looking for.
    #
    #   'jordan' -- JoFotara / ISTD. The shop states it files under the
    #               Jordan mandate. Does NOT by itself mean documents are
    #               being transmitted -- see 'unconfigured' below.
    #   'none'   -- no e-invoicing regime at all. is_enabled() below resolves
    #               FALSE regardless of the stored `enabled` value. This is
    #               NOT a pause: nothing is filed with any tax authority, and
    #               nothing is being held back to file later either.
    #
    # WHY THE DEFAULT IS 'none' AND NOT 'jordan' -- THIS SUPERSEDES THE
    # REASONING BELOW. Dated 2026-09-22, the owner's decision: e-invoicing
    # ships opt-in. The owner will turn it on per install once they have real
    # ISTD portal credentials.
    #
    # THE OLD REASONING, KEPT RATHER THAN DELETED -- this repo's convention
    # for a superseded decision (see docs/corrections/), and because this
    # file's own history is the best argument for distrusting any single
    # version of it. The default used to be 'jordan' because:
    # every install that existed then was a Jordanian install operating under
    # a live legal obligation, and not one of them had a tax_regime row
    # (get_setting() below falls back to this dict when the key is absent).
    # Defaulting to 'none' would switch e-invoicing OFF for every one of them
    # on the next upgrade, silently, with no operator action -- turning a
    # compliance feature off by shipping a patch. That read as the safer
    # choice, so the default was set to 'jordan' instead of 'none'.
    #
    # WHY THAT WAS WRONG: its premise was that defaulting to 'jordan'
    # PRESERVES COMPLIANCE. It never did. providers/direct_istd.py's
    # submit_invoice() and check_status() both `raise NotImplementedError`,
    # and the shipped provider is UnconfiguredProvider (see DEFAULTS
    # ['provider'] below) -- so an install that defaults into 'jordan' files
    # NOTHING with ISTD. All the old default actually did was mint a gapless
    # e-invoice number and an einvoice_outbox row for every sale that this
    # build can never submit. That is a WORSE position than not starting: it
    # is the APPEARANCE of compliance (numbers issued, rows queued, a status
    # screen reading "enabled") sitting on top of a growing pile of documents
    # no tax authority has ever seen, with no way out except an operator
    # noticing and either wiring real credentials or ripping the backlog out.
    # Defaulting to 'none' ships exactly what is true instead: nobody is
    # filing with anybody until an operator says otherwise.
    #
    # THE OLD REASONING'S OTHER HALF IS STILL RIGHT, AND IS ANSWERED
    # ELSEWHERE, NOT WAVED AWAY: silently switching off a shop that IS
    # genuinely using this -- real credentials wired, actually filing -- would
    # still be wrong. See schema.py's apply_einvoicing_schema(), which now
    # runs a one-time preservation migration: any company with evidence of
    # prior use (a row in einvoice_outbox, or an einvoice_sequence that has
    # advanced past zero) gets an explicit tax_regime='jordan' row written for
    # it, so an install that was really filing keeps working across this
    # upgrade. Only an install that was never actually submitting anything --
    # which, per the paragraph above, is every install that exists today --
    # changes behaviour.
    #
    # Note this is the ABSENT-KEY case only. A present-but-unrecognised value
    # is a different case and is NOT trusted as 'jordan' -- see is_enabled().
    'tax_regime': 'none',
    # 'unconfigured', not 'mock', since the default flipped to enabled. This
    # value is never dispatched on -- it is recorded onto each outbox row and
    # shown on the status screen -- so its only job is to be TRUE. A shipped
    # install runs UnconfiguredProvider (see either product's app.py), and a
    # row stamped 'mock' would have said the shop was talking to a fake
    # JoFotara it is not wired to. Measured on the running till on 2026-09-08:
    # a real sale queued a row reading provider='mock' while the app was
    # actually holding UnconfiguredProvider.
    'provider': 'unconfigured',
    'invoice_family': 'income',           # 'income' | 'general_sales'
    'default_payment_type': 'cash',       # 'cash' | 'credit'
    'seller_tin': '',
    'seller_name': '',
    # KNOWN GAP, written down rather than left to be rediscovered: this key
    # accepts a value and nothing reads it, because no client offers a field
    # for it. Both products' e-invoicing pages tell the shop to "enter the
    # Client-ID, Secret-Key and activity number" JoFotara issues
    # (products/retail/frontend/einvoicing.js, clinic's identical line, and
    # providers/unconfigured.py's _NOT_CONFIGURED_MESSAGE), but the settings
    # form collects only invoice_family/seller_name/seller_tin/currency and
    # the credentials form only client_id/client_secret. The key already
    # validates, so closing this is one label, one input and one payload
    # field per product -- frontend work, tracked outside this module.
    'seller_activity_code': '',
    'currency': 'JOD',
    # `buyer_id_required` USED TO LIVE HERE, defaulting to '1', and was read
    # by nothing anywhere in the repo. Removed rather than wired, because
    # wiring it would have been the wrong fix: the only thing that could
    # honour it is document.require_buyer_id(), which RAISES when a buyer has
    # no identifier on file, and both adapters deliberately call
    # select_buyer_id() instead so a walk-in cash sale still files (see
    # products/retail/backend/core/retail/einvoice_adapter.py's docstring).
    # Turning the setting on would therefore have blocked exactly the sales
    # Phase 1 decided must never be blocked.
    #
    # THE PHASE 1 DECISION, in writing so a later reader does not trust a
    # name that promises enforcement: always select_buyer_id, never require.
    # A setting whose name asserts an enforcement the product does not
    # perform is worse than no setting -- an operator who found it would
    # believe invoices without a buyer ID were being refused.
    'submit_interval_seconds': '60',
    'max_attempts': '20',
    'enabled_at': '',
    'istd_base_url': '',
}

_KNOWN_KEYS = frozenset(DEFAULTS.keys())


class UnknownSettingError(ValueError):
    pass


def get_setting(conn: sqlite3.Connection, company_id: int, key: str) -> str:
    if key not in _KNOWN_KEYS:
        raise UnknownSettingError(f"Unknown e-invoicing setting: {key!r}.")
    row = conn.execute(
        "SELECT svalue FROM einvoice_settings WHERE company_id=? AND skey=?", (company_id, key)
    ).fetchone()
    if row is None or row[0] is None:
        return DEFAULTS[key]
    return row[0]


def get_all_settings(conn: sqlite3.Connection, company_id: int) -> dict:
    result = dict(DEFAULTS)
    for row in conn.execute(
        "SELECT skey, svalue FROM einvoice_settings WHERE company_id=?", (company_id,)
    ).fetchall():
        skey, svalue = row[0], row[1]
        if skey in _KNOWN_KEYS and svalue is not None:
            result[skey] = svalue
    return result


def set_setting(conn: sqlite3.Connection, company_id: int, key: str, value: str) -> None:
    if key not in _KNOWN_KEYS:
        raise UnknownSettingError(f"Unknown e-invoicing setting: {key!r}.")
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO einvoice_settings (company_id, skey, svalue, updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(company_id, skey) DO UPDATE SET svalue=excluded.svalue, updated_at=excluded.updated_at",
        (company_id, key, value, now),
    )
    if key == 'enabled' and value == '1' and not get_setting(conn, company_id, 'enabled_at'):
        conn.execute(
            "INSERT INTO einvoice_settings (company_id, skey, svalue, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(company_id, skey) DO UPDATE SET svalue=excluded.svalue, updated_at=excluded.updated_at",
            (company_id, 'enabled_at', now, now),
        )


def is_enabled(conn: sqlite3.Connection, app_data_dir: str, company_id: int) -> bool:
    """The single source of truth every enqueue/worker/route call must go
    through -- never re-implement this precedence check elsewhere."""
    import os
    if os.environ.get('AURA_EINVOICING_DISABLED') == '1':
        return False
    if killswitch.is_disabled(app_data_dir).disabled:
        return False
    # LAYER 3 -- the shop's tax regime. Checked BEFORE the `enabled` toggle on
    # purpose: `enabled` only means something INSIDE a regime, so a shop that
    # files with no tax authority cannot meaningfully "enable" filing, and the
    # `enabled` value below is not even read in that case.
    #
    # Written as `!= 'jordan'` rather than `== 'none'` deliberately, and this
    # is the whole fail-safe: only the exact literal 'jordan' is accepted as
    # an explicit opt-in to a real regime. Everything else resolves OFF --
    # 'none', a typo an operator POSTed (routes.py stores whatever string it
    # is given, it does not validate values), a corrupt row, a value written
    # by a future version this build does not know about. An UNRECOGNISED
    # regime is not evidence that Jordan's mandate applies; treating it as
    # such would file real invoices with ISTD on the strength of a string
    # nobody could parse. Same fail-closed direction as `== '1'` below, which
    # test_garbage_enabled_value_fails_closed already pins as house style.
    #
    # Two different cases, and since 2026-09-22 both resolve OFF by default
    # (see DEFAULTS['tax_regime']'s own comment for the full history of why):
    # key ABSENT -> get_setting() falls back to DEFAULTS -> 'none' -> a fresh
    # install files with nobody, which is now correct. Key PRESENT but
    # unrecognised -> not trusted as 'jordan' either -> also OFF, same
    # fail-closed direction. Only an explicit, exact 'jordan' row -- written
    # by an operator, or by schema.py's one-time preservation migration for
    # an install with evidence of prior use -- resolves this layer ON.
    #
    # 'jordan' is spelled as a literal here and in the tests rather than read
    # back from DEFAULTS: comparing the code against itself could no longer
    # catch the default drifting (the same reasoning test_settings.py's
    # test_set_then_get_setting_round_trips already records for 'provider').
    if get_setting(conn, company_id, 'tax_regime') != 'jordan':
        return False
    return get_setting(conn, company_id, 'enabled') == '1'


def enabled_at(conn: sqlite3.Connection, company_id: int) -> Optional[str]:
    """The timestamp this company's feature was first turned on, or None if
    never enabled. Used by the outbox reconciliation sweep (Step 6/9) to
    guarantee pre-enablement sales/invoices are never retroactively
    submitted."""
    value = get_setting(conn, company_id, 'enabled_at')
    return value or None
