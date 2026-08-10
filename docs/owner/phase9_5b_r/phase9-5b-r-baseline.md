# Phase 9.5B-R — Baseline

## Entry gate (real, verified)

- `git status`: clean at entry.
- `git rev-parse HEAD` (before branching): `61e507ebcf2fbaf027063d40dd42d3398840f825`.
- `aura-owner-employee-management-portal-phase9-5b-complete` → `61e507e`. Matches.
- `aura-owner-commercial-operations-phase9-5a-complete` → `21be07b`. Matches (spec names this tag
  `aura-owner-commercial-operations-foundation-phase9-5a-complete` — the real tag has no `-foundation-`
  segment; same discrepancy already recorded in `docs/owner/phase9_5b/phase9-5b-baseline.md`. The real tag
  is used, not re-created under the spec's name).
- `aura-commercial-licensing-operations-phase8-complete` → `4131e610...`. Unmoved.
- `aura-owner-commercial-ops-phase8-conditional-complete` → `f593bce7...`. Unmoved.
- `aura-secure-staging-phase9-complete`: does not exist. Confirmed.
- Original `AuraEnterprise` repository: untouched this session.
- New branch: `phase9.5/owner-i18n-rtl-foundation`, from `61e507e`.

## Real baseline inherited

548/548 Owner tests passing at `61e507e`. This is the number Phase 9.5B-R's own final regression must
meet or exceed with zero failures.

## Real discovery during entry-doc reading: some spec-named docs don't exist under those exact names

`docs/owner/phase9_5b/employee-operations-api-report.md` was never created under that name — the real
file is `docs/owner/phase9_5b/employee-audit-implementation.md` covers audit; the operations API itself is
documented inline in `docs/owner/phase9_5b/final-gate-matrix.md` (gate #16) and the route module's own
docstrings (`owner/app/api_operations/routes.py`), not a standalone report file. `docs/owner/phase9_5a/
mobile-technology-adr.md` does not exist under that name either — the real file is
`docs/owner/phase9_5a/mobile-authentication-adr.md`. Both real equivalents were read in full for this
phase's required-reading step; recorded here honestly rather than fabricating matching filenames.
