# Phase 8V — Owner UI Security and Accessibility Verification

## Security (verified, not just declared)

Checked mechanically after implementation, not assumed:

- **No secrets in any new template**: `grep -rn "license_key\|key_secret_hmac\|pepper\|private_key\|DATABASE_URL" app/templates/commercial_ops/` returns nothing. The only key-shaped value ever rendered anywhere in this UI (new or pre-existing) is `License.key_prefix`/`key_suffix_masked` (already-masked, Phase 6) and device-key **fingerprints** (SHA-256 hashes, not keys).
- **No `flash()` invented**: `grep -n "flash(" app/commercial_ops/ui_routes.py` returns nothing -- every domain error re-renders the current page with `error=str(exc)`, this codebase's one existing convention (`app/auth/routes.py`), confirmed by reading it before writing a single route.
- **No `{% autoescape false %}` anywhere**: Jinja2's default escaping is never disabled in any template, new or old.
- **CSRF**: app-wide `CSRFProtect` already covers every POST; `test_create_renewal_requires_csrf` proves a missing token is rejected (400) on a real new route, not assumed from the app-wide config alone.
- **RBAC**: every route carries `@require_permission`; `test_renewal_new_form_requires_permission` and the reconciliation/notification/queue tests prove a role lacking the permission gets a real 403, not a hidden button.
- **Recent-auth/MFA**: proven, not just decorated, on every sensitive route -- `test_full_renewal_workflow_via_ui` proves approve/apply redirect to reauth without it and succeed with real MFA verification; same pattern proven for emergency-extension create, pending-activation approve, and pilot conversion.
- **Object-level lookups**: every `<uuid:id>` route resolves via `db_session.get()`, returning a real 404 (not a 500 or a leaked query) for a nonexistent row.
- **Optimistic locking**: `RenewalRequest`/`PilotRecord` both carry `version_id_col`; the apply route catches `StaleDataError` and shows a friendly retry message rather than a raw 500 or a silent double-write. `test_full_renewal_workflow_via_ui`'s double-apply assertion proves the underlying non-reentrancy (via the service layer's row lock, belt-and-suspenders with the version column).

## A finding this pass deliberately did NOT "fix"

`installations/routes.py::transition()` (pre-existing, Phase 5) lets any staff member with
`installations.update` move an installation to `DEACTIVATED`/`REPLACED` with an **optional, often
blank** reason -- the same effect Milestone 5's `release_device_slot()`/`replace_device_slot()`
achieve with a *mandatory* reason. This is real, but it is not a P0/P1: both target transitions only
ever **restrict** (free a slot), never grant anything, matching the exact reasoning already
established for why `expiry_scan.py`'s automated transitions are safe through the shared table
(Milestone 3's design doc). Tightening the generic route now would be scope creep beyond "closure and
validation," per this phase's own instruction not to redesign the Phase 8 domain. Recorded in
`phase8v-residual-risk-register.md` as a genuine, low-severity, documented gap -- the new
mandatory-reason routes are presented as the recommended path in the UI itself.

## Accessibility

Every form field has a `<label>`; every destructive action (apply renewal, revoke, reject, cancel,
release/replace a device slot, run reconciliation) has a native `confirm()` dialog plus a server-side
reason requirement where the domain calls for one -- status is never communicated by color alone
(every status also renders as text inside the badge, e.g. `<span class="badge active">ACTIVE</span>`,
not an unlabeled colored dot). Tables use real `<th>` headers. No custom keyboard trap, modal, or
JS-only interaction was introduced -- every new page is plain server-rendered HTML forms and links,
inheriting the same keyboard/focus behavior as every pre-existing Owner page (native browser form
navigation, no custom widgets). A full formal WCAG audit (screen-reader pass, contrast ratios,
focus-order verification) was not run this session -- noted honestly in the risk register rather than
claimed.

## Localization

No Arabic/RTL was added to the Owner control room. See `phase8v-scope-and-baseline.md` for why: Owner
has been English-only by consistent design across Phases 5-8 (a staff-only internal tool, distinct
from the Retail/Clinic *product* UIs, which do have `tr()`-based Arabic support and DID get new
strings this session for the PENDING-activation message -- see `product-renewal-ux-evidence.md`).
Building a from-scratch Owner-wide i18n system now would be new scope, not a closure of deferred
Phase 8 work.
