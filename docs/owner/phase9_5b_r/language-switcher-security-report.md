# Phase 9.5B-R Milestone 4 — Language Switcher Security Report

## GET is a deliberate, documented choice

`GET /locale/<code>` (`owner/app/locale_routes.py`) changes no protected business state — no employee
lifecycle, no session authority, no financial/licensing data. It is the same class of operation as this
codebase's own pre-existing plain `GET` navigation links (e.g. `staff.detail`), not the class of operation
this codebase reserves `POST`+CSRF for (lifecycle actions, session revocation, role changes — all of
which remain `POST`-only, untouched by this phase).

## Real security properties (tested)

- **Strict allowlist**: `is_supported_locale(code)` rejects anything not a key in `app.config["LANGUAGES"]`
  → `404`, never a partial match, never a filesystem lookup with the raw value.
- **No open redirect**: `_safe_redirect_target()` accepts only a `next` value starting with `/` and not
  `//` — identical discipline to `app.auth.routes._safe_next()`. An absolute URL or protocol-relative
  `//evil.com` value falls through to the safe default (`dashboard.index`).
- **No CSRF weakness introduced**: this route performs no state-changing write to any protected resource;
  Flask-WTF's global CSRF protection is irrelevant to a plain `GET`. The one write this route performs
  (updating `StaffUser.locale` for an authenticated caller) is a self-only, low-stakes preference change —
  not the kind of cross-account or destructive action CSRF protection exists to prevent, and it requires
  the caller to already be authenticated via their real session cookie (an attacker forging a cross-site
  GET to `/locale/ar?next=/` could only change the *victim's own* display language, gaining nothing).
- **Does not bypass authentication**: no `@require_login` needed or added — the route works identically
  whether or not `load_current_staff()` returns a real account, matching the explicit requirement that
  login/setup/MFA pages must be able to switch language before authentication.
- **No secret exposure**: the route touches no session token, CSRF token, or credential.

## Real, tested proof

`test_phase9_5b_r_locale_security.py`: unsupported locale → 404; absolute-URL `next` rejected (falls back
to default); protocol-relative `//` `next` rejected; anonymous switch works and sets the cookie;
authenticated switch persists to `StaffUser.locale`; switching never changes `load_current_staff()`'s
identity, permissions, or session validity (same test asserts all three before/after).
