# Phase 9.5B-R — Execution Plan

1. **String audit** — grep-driven inventory of every gated template + Python-side message (flash, WTForms,
   service errors) → classification (USER_VISIBLE/MACHINE_IDENTIFIER/etc.).
2. **Flask-Babel wiring** — `app/i18n.py` (locale selector, `babel.init_app`), `Babel(app)`, config
   (`LANGUAGES = {"en": ..., "ar": ...}`, `BABEL_DEFAULT_LOCALE = "en"`), `babel.cfg` extraction config.
3. **Locale persistence** — additive `StaffUser.locale` column (nullable, `CHECK IN ('en','ar')`); cookie
   fallback for anonymous/pre-auth pages.
4. **Language switcher** — `GET /locale/<code>` (safe, allowlisted, local-redirect-only, documented as the
   established "safe GET navigation" pattern already used elsewhere in Owner, e.g. `staff.detail`'s plain
   `GET` links) wired into `layout/base.html`.
5. **RTL foundation** — `layout/base.html` CSS rewritten to logical properties; `<html lang dir>` set from
   `g.locale`/`g.direction`; `current_locale`/`current_direction`/`is_rtl` template globals.
6. **Bidi safety** — a small Jinja filter/macro (`bidi_isolate`) wrapping identifiers (UUIDs, emails,
   employee numbers, correlation IDs) in `<bdi>`/`dir="ltr"`.
7. **Catalogs** — `owner/babel.cfg`, `owner/translations/en/LC_MESSAGES/messages.po`,
   `owner/translations/ar/LC_MESSAGES/messages.po`, compiled `.mo` files; glossary + style guide first,
   then real extraction/translation of every wrapped string.
8. **Template localization** — wrap every classified USER_VISIBLE string in the gated templates with
   `{{ _('...') }}` / `{% trans %}`; Python-side flash/error messages wrapped with `_()` at the call site.
9. **Domain label contract** — one module (`app/i18n.py` or a dedicated `app/i18n_labels.py`) mapping
   stable enum values (`ACTIVE`, `SUPER_ADMIN`, `RECENTLY_ACTIVE`, action codes) to localized display
   strings — never stored, never used in comparisons.
10. **Formatting helpers** — Babel's own `format_date`/`format_datetime`/`format_decimal` wrapped in thin
    Owner helpers that always take an explicit value (never silently reformat an identifier).
11. **Tests** — catalog compile, template render (both locales), locale resolution/persistence/security,
    hardcoded-string scanner, domain-label fallback safety.
12. **Browser validation** — real Playwright screenshots (available in this environment) at representative
    viewports, English and Arabic, for the gated screens.
13. **Accessibility** — practical checks (label association, focus, contrast) via the same Playwright
    session plus manual template review; honestly scoped (no dedicated axe-core integration exists in this
    repo — documented, not fabricated).
14. **Bilingual E2E** — one real pytest scenario per locale through the actual Flask test client.
15. **Verdict amendment + combined closure + preflight + final regression + tag.**
