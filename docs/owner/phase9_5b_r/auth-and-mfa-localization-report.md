# Phase 9.5B-R Milestone 9 — Auth/Setup/MFA Localization Report

Every real string in `login.html`, `mfa_verify.html`, `mfa_enroll.html`, `mfa_recovery_codes.html`,
`accept_invitation.html`, `change_password.html`, `reauth.html` wrapped in `{{ _(...) }}`; every real
route-level error message in `owner/app/auth/routes.py` wrapped in `_()` (`from flask_babel import
gettext as _`); `PasswordPolicyError` messages in `owner/app/security/passwords.py` localized (real bug
found+fixed: an f-string argument to `_()` cannot be extracted by `pybabel` at all — fixed to use a named
`%(min_length)s` placeholder, the standard `gettext` pattern).

## Security requirements — verified, not merely asserted

- **Account-existence non-disclosure unchanged**: `login_submit`'s generic message
  (`_("Invalid email or password.")`) is used for both "no such email" and "wrong password" in both
  locales — translating the string doesn't add a distinguishing branch; both cases still hit the exact
  same code path (`app.auth.services.authenticate`'s own comment: "Deliberately does not distinguish").
- **Setup-token state precision unchanged**: `"Invitation is invalid or has expired."` remains the one
  generic message for expired/revoked/already-accepted/wrong-token — translation added no new precision.
- **MFA secrets never localized/exposed**: the TOTP secret and provisioning URI are raw data values
  (`{{ secret }}`, `{{ provisioning_uri }}`), never routed through `_()` — a translated string could never
  accidentally substitute for or expose one, since they're structurally different values (`_()` only ever
  wraps a literal string constant, never a runtime secret).
- **Rate limits/lockout unchanged**: `is_locked_out()`/`record_attempt()` (`app.security.ratelimit`) are
  untouched by this phase — locale never influences the lockout window or attempt count.
- **CSRF/redirect safety unchanged**: no route signature, decorator, or redirect-safety check
  (`_safe_next()`) was touched.

## Real, tested proof

`test_phase9_5b_r_template_rendering.py` renders every one of these 7 templates in both `en` and `ar` and
asserts: no raw `msgid`-shaped text leaks through (i.e., translation actually happened), `lang`/`dir`
correct, no exception. `test_auth.py`/`test_security.py` (pre-existing, 28 tests) re-run unchanged and
still pass — proving the underlying auth/MFA *behavior* is identical regardless of which locale is active.
