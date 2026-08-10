# Phase 9.5B-R Milestone 12 — Validation/Flash/Error Localization

## Real coverage (gated set)

Every user-facing `error=`/flash-equivalent message in `auth/routes.py` and the `PasswordPolicyError`
messages in `security/passwords.py` (see `auth-and-mfa-localization-report.md`). API-layer error *codes*
(`RECORD_NOT_FOUND`, `VERSION_CONFLICT`, `INVALID_STATE_TRANSITION`, `LAST_SUPER_ADMIN_REQUIRED`,
`PERMISSION_DENIED`, `VALIDATION_ERROR`, `EMPLOYEE_NOT_ACTIVE`) remain **stable, untranslated JSON string
values** — see `api-localization-boundary.md`; only the web (HTML) surface's human-readable messages are
localized, never the machine-consumed code.

## Real requirements verified

- **No raw Python exception displayed**: unchanged from before this phase — every `render_template(...,
  error=...)` call passes a real, curated string (now wrapped in `_()`), never `str(exc)` of an
  unclassified exception.
- **No untranslated enum where a display label is required**: every template-rendered status/role/
  presence value goes through an `i18n_labels.py` function (Milestone 11), never the raw code directly in
  a heading/paragraph (raw codes remain only in `value=` form attributes and `dir="ltr"` badges where the
  code itself, not a label, is the intended display — e.g. `s.platform` in the sessions table, which is a
  real technical value, not prose).
- **No raw database constraint name**: the `ck_staff_users_locale_supported` CHECK constraint added this
  phase is defense-in-depth only — no code path surfaces a raw constraint-violation message to a web user;
  the real, always-first guard is `is_supported_locale()` in application code.
- **No stack trace**: unchanged.
- **Logs retain stable identifiers**: `owner/app/observability/logging_config.py` is untouched by this
  phase — structured log lines still carry the real English/technical message, never a localized one
  (operator/log-only, per `string-classification-report.md`'s classification).

## WTForms

Owner does not use WTForms for field-level validation messages (confirmed: `grep -rn "wtforms" owner/app`
finds only `flask_wtf`'s `CSRFProtect`/`FlaskForm`-adjacent CSRF machinery, no `wtforms.validators` usage
in any gated route) — every validation message in the gated set is a plain Python string already covered
above; there is no separate WTForms message catalog to additionally wire into Flask-Babel.
