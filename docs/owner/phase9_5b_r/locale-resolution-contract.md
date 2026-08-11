# Phase 9.5B-R Milestone 3 — Locale Resolution Contract (real code)

`owner/app/i18n.py::select_locale()` — the one canonical authority (Non-Negotiable Principle 1), wired as
Flask-Babel's `locale_selector`. Real precedence, in order:

1. `session["locale"]` — set only by the validated `/locale/<code>` switcher route (never read raw from
   `request.args` here).
2. Authenticated `StaffUser.locale` (persisted preference).
3. `request.cookies["owner_locale"]` — anonymous/pre-auth continuity across sessions.
4. `request.accept_languages.best_match(["en", "ar"])`.
5. `BABEL_DEFAULT_LOCALE` ("en").

Every step is gated by `is_supported_locale()`, which checks membership in `app.config["LANGUAGES"]`
(`{"en": ..., "ar": ...}`) — a strict allowlist, never a regex/pattern match, never a filesystem path
(Non-Negotiable Principle 5). No step trusts a raw client-supplied string without this check.

## Real security properties (tested, `test_phase9_5b_r_locale_security.py`)

- `/locale/xx` (unsupported) → 404, never a crash, never a silently-accepted invalid locale.
- `/locale/../../etc` → Flask's own URL routing rejects this before it ever reaches `switch()` (the
  `<code>` URL converter is a plain string segment, not a path; a `/` in the value simply doesn't match the
  route at all).
- No locale value is ever used to construct a filesystem path directly — `BABEL_TRANSLATION_DIRECTORIES`
  is a fixed, server-side constant; Flask-Babel resolves `ar`/`en` to `translations/ar/LC_MESSAGES/
  messages.mo`/`translations/en/LC_MESSAGES/messages.mo` internally using its own trusted logic, never
  string-concatenating the raw request value into a path Owner code touches directly.

## Business logic unaffected

`select_locale()` is called exactly where Flask-Babel needs it (locale selection for `gettext`/
`format_date`/etc.) and in the one `context_processor` that exposes `current_locale`/`current_direction`/
`is_rtl` to templates. It is never called from, or consulted by, `app.security.rbac`, `app.employees.
services`, `app.auth.services`, or any state-transition function — confirmed by grep (`app.i18n` is
imported only by `app/__init__.py`, `app/locale_routes.py`, and the label/formatting helper modules that
themselves only format for display).
