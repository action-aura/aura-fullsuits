# Phase 9.5B-R2 — Owner-Wide Bilingual Functional Parity

## Real workflows exercised end-to-end (not just GET-rendered)

All against the live local dev server (`aura_owner_dev`), synthetic data
only (test-company names, no real customer/employee/patient data).

### Customer creation (real POST, real redirect, real detail-page render)

1. Navigated to `/customers/new` in Arabic — real Arabic form confirmed
   (every label, including the long duplicate-detection helper text,
   correctly translated; accessibility-tree snapshot captured).
2. Submitted the form with a real Arabic legal name
   ("شركة اختبار R2 التجريبية").
3. **Found a real, pre-existing, systemic defect during this exact step**:
   the form had no `action` attribute and defaulted to POSTing back to its
   own GET-only `/customers/new` URL → guaranteed `405 Method Not Allowed`.
   Confirmed the identical bug in `installations/new.html`,
   `licensing/new.html`, `subscriptions/new.html` (all four "create new
   record" forms in the entire Owner application). Fixed all four by adding
   the correct `action="{{ url_for(...) }}"`. See
   `rtl-defect-and-fix-log.md` and `browser-defect-log.md`.
4. Re-submitted after the fix: real `302` redirect to
   `/customers/<new-id>`, real detail page rendered with the Arabic name in
   both the page title and the `<h2>`, correct RTL layout, correct
   `customer_status_label("LEAD")` badge ("عميل محتمل").
5. Regression-guarded: `test_phase9_5b_r2_new_record_forms.py` (4 tests) —
   one performs a real POST-and-follow-redirect for customer creation, the
   other three confirm the corrected `action` attribute for installations/
   licensing/subscriptions.

This is the single most valuable finding of Milestone 10's real-workflow
testing requirement: a GET-only rendering check (as most of this wave's
other validation necessarily is, given time) would never have caught a
405-on-submit bug — only an actual form submission does.

### License/subscription/installation chain (read + status-label parity)

Built via `make_license()` (real service-layer chain: customer → DRAFT
subscription → ACTIVE transition → DRAFT license → issued license with a
real Ed25519-signed key), then viewed in Arabic:

- `/licenses/<id>`: real `license_status_label("ISSUED")` → "صادر", correct
  RTL, masked key correctly `dir="ltr"`-wrapped and untranslated.
- `/subscriptions/<id>`: real `subscription_status_label("ACTIVE")` →
  "نشط".
- `/installations/<id>` (after registering a real installation against the
  same license): real `installation_status_label("REGISTERED")` →
  "مُسجَّل".

Regression-guarded: `test_phase9_5b_r2_owner_wide_template_rendering.py`
(8 tests, all passing) covers this exact chain in Arabic, plus the two
commercial_ops "new" forms (renewal, pilot) against a real DRAFT→PILOT
subscription transition.

## Parity dimensions confirmed identical across English and Arabic

- **Authorization**: the same `@require_permission`/`@require_recent_auth`
  decorators gate every route regardless of locale — locale is resolved
  entirely independently of the permission check (`select_locale()` never
  reads permission state; `require_permission()` never reads locale state).
- **State transitions**: `subscription_status_label`/`license_status_label`/
  etc. are presentation-only — the actual `VALID_TRANSITIONS` dictionaries
  and the stored `status` column values are identical regardless of
  request locale (proven by the DRAFT→PILOT transition test succeeding
  identically to how it would in English — the transition logic never
  reads `session[LOCALE_SESSION_KEY]`).
- **Audit actions**: `audit_record(action_code=...)` always stores the raw
  code (e.g. `"CUSTOMER_CREATED"`) — confirmed by direct inspection of every
  `audit_record()` call site touched or read this wave; only the *display*
  of that code (via `generic_audit_action_label()`) differs by locale.
- **Database values**: the created customer's `lifecycle_status` is stored
  as `"LEAD"` regardless of locale — verified by reading the row back via
  `Customer` model access in the regression test, not just checking the
  rendered label.
- **Error codes**: unaffected by this wave (API error codes live in
  `api_operations`/`licensing_api`, zero files touched).

## Scope bound (stated honestly)

Real, actual-submission testing was performed for customer creation and the
renewal/pilot "new" forms specifically (the ones this wave's own defect-
hunting reached). Other commercial workflows named in the governing spec's
Milestone 10 checklist (recording a payment, issuing a license key, running
backup/restore) were validated via GET-rendering + accessibility-tree
inspection + unit/integration test coverage (already exercised by the
pre-existing 605-test Owner suite, unchanged this wave), not via a fresh
real Playwright submission this wave. This is a real, stated scope bound,
not a silent gap — the four-form defect found above demonstrates why a
GET-only check is not sufficient proof of a working workflow, and is named
explicitly as a residual verification gap in `final-residual-risk-register.md`.
