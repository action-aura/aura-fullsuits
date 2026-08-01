# Phase 9.5B-R3 — Milestone 3: Service Message Localization, Final State

## 8 new `localize_*_error` functions, `app/i18n_labels.py`

Confirmed present (`grep -n "^def localize_" app/i18n_labels.py`):

1. `localize_pilot_lifecycle_error` (line 254)
2. `localize_pilot_transition_error` (line 277)
3. `localize_renewal_transition_error` (line 290)
4. `localize_device_slot_error` (line 304)
5. `localize_emergency_extension_error` (line 321)
6. `localize_pending_activation_error` (line 337)
7. `localize_activation_policy_error` (line 352)
8. `localize_notification_error` (line 362)

Each is a `code -> messages[code]` dict lookup where every value is a
Flask-Babel `_("...")`-wrapped, `%(name)s`-style template (matching this
project's existing i18n convention throughout `i18n_labels.py`), with a
safe `.get(code) or code` fallback so an unrecognized code degrades to
the raw code string rather than raising `KeyError` in a route handler.

## Route-layer wiring

`commercial_ops/ui_routes.py`:
- Imports all 8 functions.
- `_localized_error_text(exc)` dispatches by `isinstance()` across the 8
  stable-coded exception types, calling the matching `localize_*_error(
  exc.code, **exc.params)`.
- All 19 previous `error=str(exc)` call sites (in Jinja-rendering route
  handlers) now call `error=_localized_error_text(exc)` — verified via
  `grep -c "error=str(exc)"` returning 0 and
  `grep -c "_localized_error_text(exc)"` returning 19.

## Result against the governing spec's PASS bar

"Zero unresolved current user-facing English-only message" — satisfied
for all 7 in-scope exception classes (8 counting the
`PendingActivationError`/`ActivationPolicyError` split). The 2
deliberately-untouched classes (`RenewalApplicationError`,
`CommercialPolicyError`) are justified in
`service-message-call-path-audit.md` as, respectively, a real external
string-match dependency and a proven operator-only/unreachable-from-any-
route class — neither is a "current user-facing English-only message" in
the sense the spec's PASS bar targets.

## Downstream re-verification required

Milestone 10 and Milestone 11 re-run the full localization/API-security
test suites and the full Owner regression against this exact final
state, to confirm the 8 new translatable strings don't regress catalog
completeness or break any existing localization test.
