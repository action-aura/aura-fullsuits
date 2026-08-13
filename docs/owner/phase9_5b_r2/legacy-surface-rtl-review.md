# Phase 9.5B-R2 — Legacy-Surface RTL Review

## Method

Every one of the 50 newly translated templates was edited using the exact
same RTL pattern already proven correct in Phase 9.5B-R (`layout/base.html`'s
CSS logical properties, inherited automatically by every template that
extends it — nothing in this wave touched `layout/base.html`'s CSS itself).
Per-template review covered:

- Tables: every `<table>` in the 50 templates was given a real `<thead>`/
  `<tbody>` split (the same real bug class found and fixed in Phase 9.5B-R's
  mobile-collapse defect — checked here proactively rather than found again).
- Forms/labels: `<label>`/`<input>` pairs use the shared CSS (no new
  physical left/right positioning was introduced anywhere in this wave).
- Status badges: all use the existing `.badge` class, unchanged.
- Mixed-direction identifiers: every UUID, license key, key prefix/suffix,
  fingerprint, checksum, product code, plan code, email, phone, IP address,
  staff-user ID, subscription/installation/license/renewal/pilot ID shown in
  the 50 templates is wrapped in `<bdi dir="ltr">` (or a plain `dir="ltr"` on
  the containing `<code>` element for monospace values) — this is the single
  largest recurring pattern applied this wave, checked template-by-template
  during translation, not as a separate pass.
- Confirmation dialogs: 10 real `onsubmit`/`onclick` `confirm(...)` call
  sites (the same real injection-risk class fixed once in Phase 9.5B-R)
  found and fixed across `staff/detail.html`, `customers/detail.html`,
  `installations/detail.html`, `licensing/detail.html` (×2),
  `licensing_admin/signing_keys.html`, `licensing_admin/device_keys.html`,
  `system/backups.html`, `subscriptions/detail.html`,
  `commercial_ops/renewal_detail.html` (×3),
  `commercial_ops/pilot_detail.html`, `commercial_ops/activation_policy.html`,
  `commercial_ops/emergency_extensions_new.html`,
  `commercial_ops/emergency_extension_detail.html`,
  `commercial_ops/pending_activation_detail.html`,
  `commercial_ops/reconciliation.html` — replaced with the existing
  `data-confirm` + `static/js/confirm.js` CSP-compliant pattern in every
  case, not a new mechanism.
- Icon direction: no directional icons (arrows meant to indicate
  navigation/flow direction, not just visual separators) were found in the
  50 templates. The `&rarr;` character used between two date/status values
  (e.g. "start → end") is decorative punctuation, not a navigational icon —
  allowlisted in the hardcoded-string scanner, not mirrored.

## Real defect found and fixed this wave

`commercial_ops/timeline.html`, `commercial_ops/reconciliation.html`, and
the four status-transition tables (subscriptions/licensing/installations/
renewals/pilots detail pages) originally rendered raw, untranslated status
codes directly from Python service layers (`timeline.py`'s f-string event
descriptions). These were fixed at the source (`app/commercial_ops/timeline.py`,
`app/commercial_ops/pilot_lifecycle.py`, `app/commercial_ops/renewal_requests.py`)
to build real, localized sentences via `gettext()` and the existing
domain-label functions, rather than leaving hardcoded English text baked
into service-layer output. See `owner-wide-validation-and-message-report.md`.

## No new visual/layout defects found

Because this wave reused the identical, already-proven logical-property
foundation rather than writing new CSS, no new RTL layout defect (of the
kind Phase 9.5B-R found in its mobile-table-collapse bug) was introduced.
Real confirmation of this claim is in `full-viewport-browser-validation.md`
(Milestone 8), not asserted from code review alone.
