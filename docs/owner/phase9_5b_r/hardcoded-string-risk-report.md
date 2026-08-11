# Phase 9.5B-R — Hardcoded String Risk Report

## Real risk this phase closes

Every USER_VISIBLE string in the gated set (187, `string-classification-report.md`) is currently a raw
English literal in a Jinja template or a Python `render_template(..., error="...")` call — 100% hardcoded,
0% translatable, before this phase's work. This is the exact risk Phase 9.5B's own gate matrix flagged as
`NOT VERIFIED / DEFERRED`.

## Risk if left unaddressed

An Arabic-speaking employee/manager would see 100% English UI with no `lang`/`dir` correctness — not a
partial-translation risk but a total-absence risk. This phase's Milestone 16 hardcoded-string scanner
(`hardcoded-string-control.md`) is the regression guard that keeps this closed going forward: any new
raw-literal string added to a gated template after this phase fails the scanner test.

## Real risk NOT addressed this phase (documented, not silent)

The 37 non-gated Phase 5-8 templates remain 100% hardcoded English, by the scope decision in
`phase9-5b-r-scope-and-boundaries.md`. The hardcoded-string scanner explicitly excludes them (directory
allowlist) so it doesn't produce 37 templates' worth of false "failures" for a decision already made and
documented — this is a reviewed allowlist entry, not a scanner weakness.

## Scanner design constraint (informs Milestone 16)

Per the governing spec's own instruction ("do not create a brittle test that flags every source literal
indiscriminately"), the scanner targets only genuine user-visible text nodes/attributes in the gated
template set — it does not flag: Jinja control syntax, CSS, HTML attribute names, `url_for()` route names,
model/field names, or any of the MACHINE_IDENTIFIER strings classified above.
