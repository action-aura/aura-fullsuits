# Phase 9.5B-R2 — RTL/Localization Defect and Fix Log

Real defects found during this wave's translation and review pass, in the
order found. Each was fixed at its actual source, not worked around.

## 1. Insecure inline `confirm()` JS pattern (15 real occurrences)

**Where**: `staff/detail.html`, `customers/detail.html`,
`installations/detail.html`, `licensing/detail.html` (×2),
`licensing_admin/signing_keys.html`, `licensing_admin/device_keys.html`,
`system/backups.html`, `subscriptions/detail.html`,
`commercial_ops/renewal_detail.html` (×3), `commercial_ops/pilot_detail.html`,
`commercial_ops/activation_policy.html`,
`commercial_ops/emergency_extensions_new.html`,
`commercial_ops/emergency_extension_detail.html`,
`commercial_ops/pending_activation_detail.html`,
`commercial_ops/reconciliation.html`.

**Defect**: `onsubmit="return confirm('...')"` / `onclick="return confirm('...')"`
interpolates a value directly into a single-quoted JS string literal inside
an HTML attribute — the same real risk class Phase 9.5B-R already found and
fixed once in `employees/detail.html`, present in 15 more places this wave's
audit reached that Phase 9.5B-R's narrower scope never touched.

**Fix**: replaced every instance with `data-confirm="{{ _(...) }}"` read by
the existing, CSP-compliant `static/js/confirm.js` — the same established
fix, not a new mechanism.

## 2. Real hardcoded English sentences generated server-side

**Where**: `app/commercial_ops/timeline.py` (5 f-string event descriptions),
`app/commercial_ops/pilot_lifecycle.py` (1 raised-exception message),
`app/commercial_ops/renewal_requests.py` (2 raised-exception messages).

**Defect**: these Python service functions built real, user-facing English
sentences (`f"Subscription {from_status} -> {to_status}"`, exception
messages later shown via `error=str(exc)` in templates) that were never
routed through `gettext()` — a hardcoded-string-scanner blind spot because
the scanner only inspects `.html` files, not `.py` service code.

**Fix**: wrapped in `gettext()` with named placeholders, and the raw status
codes are now passed through the same domain-label functions
(`subscription_status_label`, `license_status_label`, etc.) used everywhere
else, so these generated sentences render fully localized status names too.

## 3. Real, documented residual — notification titles are stored, not
   render-time strings

**Where**: `app/commercial_ops/reconciliation.py`, `device_slot_ops.py`,
`expiry_scan.py` build `InternalNotification.title` from English f-strings
at write time (e.g. when a scheduled reconciliation job runs), then the
title is persisted and displayed later, possibly to a different staff member
in a different locale.

**Decision**: NOT wrapped in `gettext()`. Unlike every other fix in this
log, this is not a simple missed-wrap — these titles are computed once and
stored, often outside any live HTTP request/locale context (e.g. a scheduled
job). Naively wrapping them in `gettext()` would silently freeze whichever
locale happened to be active at write time (usually English, since
reconciliation typically runs from a script/cron) and would not
retroactively translate for a reader viewing in Arabic later — a real
architecture change (store structured data, render the sentence at display
time) is required to do this correctly, which is out of this wave's
translation-focused scope. Documented as a residual limitation in
`final-residual-risk-register.md`, not silently ignored.

## 4. Scope-document corrections (not code defects, but real findings)

Three claims in an early draft of `phase9-5b-r2-scope-and-boundaries.md`
were wrong (based on blueprint/route naming rather than reading every
template): Products, Add-ons, Entitlement definitions, and Price History
all DO exist as real current Owner surfaces (inside `catalog/index.html`
and `catalog/plan_detail.html`), contrary to the initial assessment.
Corrected in that document with the real evidence, not silently fixed.

## 5. `pybabel update` fuzzy-mismatched 221 new strings to wrong existing
   translations

**Where**: `translations/{en,ar}/LC_MESSAGES/messages.po`.

**Defect**: Babel's approximate-match heuristic paired 221 genuinely-new
msgids (e.g. `'Pilot'`) with unrelated pre-existing translations (e.g. the
old translation of `'Pilots'`), marking them `fuzzy` with a wrong guessed
`msgstr` rather than leaving them empty. Because these entries were not
empty, a script that only filled `not m.string` entries silently missed
all 221 — they would have compiled with the wrong text (or been silently
excluded by `pybabel compile`'s default fuzzy-skipping behavior).

**Fix**: caught by inspecting `m.fuzzy` explicitly (not just emptiness)
after the update step; all 221 given correct, freshly authored English
identity / real Arabic translations and the fuzzy flag cleared. Verified
by a post-fix integrity check: 742 total messages, 0 empty, 0 fuzzy, in
both catalogs.
