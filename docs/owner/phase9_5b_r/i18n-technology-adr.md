# Phase 9.5B-R Milestone 2 — i18n Technology ADR

## Decision: Flask-Babel 4.0.0 (wrapping Babel 2.18.0)

## Options considered

**A. Flask-Babel** (chosen). The canonical Flask/Jinja i18n integration — wraps the real, mature,
widely-deployed GNU `gettext` catalog format (`.po`/`.mo`) via the `Babel` library (used by Django, Sphinx,
and most of the Python web ecosystem for two decades). Provides: `_()`/`gettext()`/`ngettext()` in both
Python and Jinja, `pybabel extract/update/compile` CLI, real pluralization rules (CLDR-based, correct for
both English and Arabic's more complex plural forms), `format_date`/`format_datetime`/`format_decimal`
locale-aware formatting, a documented `Babel.localeselector`-equivalent (`Babel(app, locale_selector=...)`
in 4.x) hook — exactly the "explicit, bounded locale negotiation" this phase's own principles require.

**B. Ad-hoc dict-based translation** (rejected). Explicitly forbidden by Non-Negotiable Principle 1 ("do
not create separate translation dictionaries per feature... a route-specific translation engine"). Would
also have no real pluralization support and no extraction tooling — every new string would need manual
dictionary maintenance in two places forever.

**C. python-i18n / other JSON-catalog libraries** (rejected). Less mature Flask/Jinja integration than
Flask-Babel, no CLDR pluralization, smaller maintenance community. No clear advantage over the
`gettext`-based standard for a server-rendered Flask app with zero JavaScript (confirmed,
`current-owner-string-inventory.md`).

## Real dependency impact

Two new packages: `flask-babel==4.0.0`, `babel==2.18.0` (Babel is Flask-Babel's own real dependency, not a
separate choice — `pytz` is Babel's transitive dependency, unpinned like every other transitive dependency
in this codebase's existing `requirements/*.txt` convention). `pip_audit` run immediately after
installation: **zero known vulnerabilities** in `flask-babel`, `babel`, or `pytz` (the only pre-existing
findings, `pytest`/`setuptools`, are unrelated and pre-date this phase).

## Catalog paths / commands (real, verified working this phase)

- Extraction config: `owner/babel.cfg`
- Catalog template: `owner/translations/messages.pot`
- English catalog: `owner/translations/en/LC_MESSAGES/messages.po` (+ compiled `.mo`)
- Arabic catalog: `owner/translations/ar/LC_MESSAGES/messages.po` (+ compiled `.mo`)
- Extract: `pybabel extract -F babel.cfg -o translations/messages.pot .`
- Init (first time only): `pybabel init -i translations/messages.pot -d translations -l ar`
- Update (after extraction changes): `pybabel update -i translations/messages.pot -d translations`
- Compile: `pybabel compile -d translations`
- All four are wrapped as `flask` CLI commands in `owner/app/cli.py` (`flask translations extract/update/
  compile`) — see `translation-catalog-maintenance.md`.

## Fallback behavior

Missing Arabic key → Flask-Babel's own `gettext()` fallback returns the English `msgid` verbatim (never a
raw translation key, never a crash) — verified in `test_phase9_5b_r_i18n_infrastructure.py`.

## English is the source locale

Confirmed per Non-Negotiable Principle 2: every `msgid` in the `.pot`/`.po` files is the real English UI
string itself (standard `gettext` convention), not an abstract key — matches "English is the source/
reference locale."
