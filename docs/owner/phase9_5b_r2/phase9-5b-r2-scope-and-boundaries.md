# Phase 9.5B-R2 — Scope and Boundaries

## In scope (real, currently implemented, currently reachable)

All 50 not-yet-localized Owner templates, grouped by the real blueprints that
serve them (see `phase9-5b-r2-baseline.md` for the exact counts):

- `catalog` (5 templates: index, plan_new, plan_detail, versions, channels —
  versions/channels also served by `releases` blueprint, same files)
- `commercial_ops` (19 templates: renewals, pilots, emergency extensions,
  pending activations, activation policy, slot exceptions, notifications,
  queue, reconciliation, timeline, not_found)
- `customers` (3 templates: list, new, detail)
- `dashboard` (1 template: index — the Owner-wide main dashboard)
- `installations` (3 templates: list, new, detail)
- `licensing` (3 templates: list, new, detail)
- `licensing_admin` (6 templates: status, signing_keys, requests, device_keys,
  offline_policies, entitlement_preview)
- `staff` (3 templates: list, detail, invitation_created — staff/security
  administration, distinct from the `employees` self-service portal already
  localized in Phase 9.5B-R)
- `subscriptions` (3 templates: list, new, detail)
- `system` (1 template: backups — this repository has no separate "restore"
  page; restore is an action button on the same `system/backups.html` page,
  confirmed by reading `app/system/routes.py`)
- `audit` (3 templates: list, security_events, verify_chain)

Also in scope: hardcoded English `error=` strings and `entity=` labels passed
from `app/commercial_ops/ui_routes.py` into templates (found during Milestone
1 discovery — real, currently untranslated user-facing strings; documented in
`owner-wide-validation-and-message-report.md`).

## Real correction — payments (found during permission-decorator verification)

Payments are NOT a separate blueprint/page, but they ARE a real, reachable
current surface: `POST /subscriptions/payments` (`payments.create` permission)
and `POST /subscriptions/payments/<id>/correct` (`payments.correct`
permission) in `app/subscriptions/routes.py`, exposed via a "Record a
payment" form embedded directly in `subscriptions/detail.html` (no payment
list/history view exists on that page — confirmed by full-file grep, only the
recording form is present). This form's labels are in scope as part of
translating `subscriptions/detail.html` (already counted in the 50-template
total); no separate payments template exists to translate.

## Correction — Products, Add-ons, and Entitlement definitions DO exist

An earlier draft of this document (written before reading `catalog/index.html`
itself, based only on blueprint/route-file names) incorrectly stated that
Products, Add-ons, and Entitlement definitions were not implemented. Reading
the actual template shows this was wrong. The real state, corrected here:

- **Products**: `catalog/index.html` renders a real, populated Products table
  (code, name, commercial status, sellable flag) sourced from the `catalog`
  blueprint. Read-only from this view (no dedicated products blueprint exists
  separately — it is one section of the shared catalog page), but it is a
  real, reachable, current surface and is in scope for translation.
- **Add-ons**: the same page renders a real Add-ons table with a live
  availability-status management form (`POST` via an auto-submitting
  `<select>`, real transition options DRAFT/PLANNED/PILOT/AVAILABLE/RETIRED).
  This is a genuine management surface, not read-only. In scope.
- **Entitlement definitions**: the same page also lists entitlement
  definitions (code, value type, description) — read-only in this view.
  `licensing_admin/entitlement_preview.html` is a separate, different screen
  (a resolved-entitlement preview for one specific license, not the
  definition list). Both are real and both are in scope.

All three live inside the single `catalog/index.html` template, already
counted in the 50-template/`catalog` (5) total — no new template count
change results from this correction, only a correction to which domains were
believed unimplemented.

## Confirmed NOT present in this codebase (do not build)

- **Platforms as a separate managed catalog**: platforms exist only as a
  simple lookup (`platform_id`/`platform_code`) referenced by installations
  and catalog versions/channels — no dedicated platform-management CRUD
  surface was found.
- **Settings**: no `settings` blueprint or template found.

## Second correction — Price history DOES exist

`catalog/plan_detail.html` has a real "Price history" section: a table of
all historical prices (base price, currency, effective-from/until, append-only
— never overwrites) plus a real "Add a new price" form. This was also
wrongly listed as absent in an earlier draft of this document, for the same
reason (judged from blueprint/route names, not from actually reading every
template). Corrected here; in scope, already counted in the `catalog` (5)
total.

Both corrections in this document exist because the audit initially reasoned
from route/blueprint naming conventions rather than reading every template
body. This is recorded honestly rather than silently fixed, consistent with
this session's discipline of surfacing every real discrepancy found.

Per Non-Negotiable Rule 2 ("do not implement future Phase 9.5C features
merely because documentation or foundation models mention them") and Rule 5
("only translate interfaces that actually exist"), these are recorded here as
confirmed-absent and out of scope, not built, not translated, not counted in
any coverage total.

## Explicitly forbidden (unchanged from Phase 9.5B-R)

Leads, Customer GPS/interactions/follow-ups, Quotes, Orders, Invoices,
commissions, expenses, management shared notes, Flutter/Android/iOS Owner
apps, remote deployment, Phase 9R, Phase 9.5C, Clinic/Retail feature changes,
licensing-contract changes, automated translation, real personal data.
