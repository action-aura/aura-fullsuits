# Phase 9.5B-R Milestone 5 — HTML Language/Direction Contract

`owner/app/templates/layout/base.html:2`: `<html lang="{{ current_locale }}" dir="{{ current_direction }}">`
— every gated page (auth, setup/invitation, MFA, employee portal, self-service, error pages) extends this
one template, so every one of them gets the correct attribute pair automatically, from one place
(Non-Negotiable Principle 1/10).

## Template globals exposed (`app/i18n.py::init_app()`'s `context_processor`)

- `current_locale` — `"en"` or `"ar"`.
- `current_direction` — `"ltr"` or `"rtl"`.
- `is_rtl` — boolean convenience.
- `supported_locales` — `{"en": "English", "ar": "العربية"}`, used to render the switcher without
  hardcoding the locale list a second time anywhere.

Nothing mutable is exposed — these are plain, request-scoped, read-only values computed fresh each
request from `select_locale()`; no template can accidentally change the active locale by mutating them
(Jinja context values aren't write-back to Python state).

## Real, tested proof

`test_phase9_5b_r_locale_security.py`/`test_phase9_5b_r_template_rendering.py`: every gated template
renders `<html lang="en" dir="ltr">` by default and `<html lang="ar" dir="rtl">` after a locale switch —
checked directly against the raw HTML response, not inferred.
