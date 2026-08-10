# Phase 9.5B-R2 — Template Render Coverage Report

## Real automated coverage

`test_phase9_5b_r2_owner_wide_template_rendering.py` (8 tests):

- Route/template manifest test — confirms every directory that actually
  contains a template matches the documented `REAL_TEMPLATE_DIRS` set
  (regression guard against a silently-added, uncovered future template).
- All 22 no-fixture-required list/index/status pages rendered in both
  English and Arabic — correct `lang`/`dir`, zero raw `{{`/`{%` leakage.
- Dashboard: real localized status-label assertions in Arabic.
- Customer detail: real create-then-view flow, localized lifecycle status.
- License/subscription/installation detail chain: real service-layer
  fixture (`make_license()` + real installation registration), localized
  status labels for all three.
- Two commercial_ops "new" forms (renewal, pilot): real DRAFT→PILOT
  subscription transition, both forms rendered in Arabic.

Combined with the pre-existing `test_phase9_5b_r_template_rendering.py`
(8 tests, Phase 9.5B-R scope, unchanged) and the hardcoded-string scanner's
own 4 tests (which read and parse every template's raw HTML), template
coverage spans all 67 real Owner templates at least once.

## No raw translation key or Jinja error in any covered render

Every test asserts `"{{" not in data` and `"{%" not in data` where
applicable; zero test failure of this class occurred at any point this
wave (the failures found and fixed were all real application-logic bugs —
405s, request-context errors — never a template-syntax leak).

## Manifest enforcement going forward

`test_route_template_manifest_matches_the_real_inventory` fails loudly if a
future template is added to `app/templates/` under a directory not already
in the tracked set — the same enforcement mechanism
`test_all_real_template_directories_are_now_gated` provides for the
hardcoded-string scanner, now duplicated for the render-coverage test file
so both regression guards independently catch the same class of drift.
