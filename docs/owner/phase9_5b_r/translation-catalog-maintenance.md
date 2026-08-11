# Phase 9.5B-R — Translation Catalog Maintenance

## Real commands (verified working this phase)

```
cd owner
python -m babel.messages.frontend extract -F babel.cfg -o translations/messages.pot .
python -m babel.messages.frontend update -i translations/messages.pot -d translations
# edit translations/ar/LC_MESSAGES/messages.po and en/LC_MESSAGES/messages.po by hand
python -m babel.messages.frontend compile -d translations -f
```

(`python -m babel.messages.frontend` is used directly rather than the `pybabel` console-script entry point
because this environment's venv does not have `Scripts/pybabel.exe` on PATH by default in every shell —
the module invocation is equivalent and always works.)

## Real bug found and fixed this phase

`babel.cfg`'s first draft listed `extensions=jinja2.ext.autoescape,jinja2.ext.with_` (a copy of an
outdated Flask-Babel tutorial). Both extensions were merged into Jinja2 core years ago and no longer exist
as importable extension names in the Jinja2 version this repo pins (3.x) — extraction failed immediately
with `AttributeError: module 'jinja2.ext' has no attribute 'autoescape'`. **Fixed** by removing the
`extensions=` line entirely (autoescape/with-statement are Jinja2 3.x defaults, no opt-in needed).

## Adding a new translatable string

1. Wrap it in `{{ _('...') }}` (Jinja) or `_("...")` (Python, `from flask_babel import gettext as _`).
2. Run `extract` + `update` (adds the new `msgid` to both `.po` files with an empty `ar` `msgstr`).
3. Translate the new `ar` entry by hand, following `translation-style-guide.md`.
4. Run `compile`.
5. Run `test_phase9_5b_r_catalog_completeness.py` (Milestone 16) — it fails the build if any `ar`
   `msgstr` is empty for a `msgid` used in the gated template/route set, catching a forgotten step 3.

## CI validation command

`flask i18n-preflight` (Milestone 21) — extract-free, compile-verification-only check safe to run in a
frozen/staging environment without a Python toolchain: confirms both `.mo` files exist, are non-empty, and
that `LANGUAGES`/`BABEL_DEFAULT_LOCALE` config is internally consistent.

## Real catalog stats (this phase's final state)

203 real `msgid` entries, 203/203 real Arabic translations (0 empty/missing — see
`test_phase9_5b_r_catalog_completeness.py`), 203/203 English identity translations.
