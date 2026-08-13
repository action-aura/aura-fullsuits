# Phase 9.5B-R — Final Residual Risk Register

## Real, deliberate deferrals (carried or new)

1. **37 non-gated Phase 5-8 templates remain English-only.** Real risk: low today (no reported Arabic-
   speaking-staff need on those specific screens yet); grows if/when those screens see real Arabic-
   speaking users before a future phase extends localization to them. Mitigation ready: the foundation
   (catalogs, helpers, RTL layout) requires zero rework — just wrapping strings.
2. **No mobile client, no `/api/operations/v1/auth/mobile/*` routes.** Unchanged from Phase 9.5A/9.5B.
3. **Responsive/accessibility validation is real but bounded** (desktop+mobile, not every viewport×screen
   combination; accessibility-tree-based, not a scored axe-core audit). Real risk: low — the one defect
   that existed in the tested surface (mobile table collapse) was found and fixed; an untested combination
   *could* theoretically hide a similar issue, but the design pattern (logical CSS properties, applied
   uniformly via the shared layout) makes a per-screen defect unlikely by construction.
4. **Currency/number formatting untested for currency values** — genuinely not applicable (no monetary
   value exists in the gated scope this phase), not a gap to track.
5. **Heartbeat rate limiting** (carried from Phase 9.5B, unrelated to this phase, unchanged).

## Real gaps found and fixed during this phase (residual risk: none — closed)

- `babel.cfg` referenced obsolete Jinja2 extensions, breaking extraction entirely — **fixed**.
- Two inline `onsubmit="return confirm('...')"` strings interpolated translated text directly into a
  JS string literal — a real breakage/injection risk — **fixed** (moved to `data-confirm` + external
  script).
- `PasswordPolicyError`'s f-string argument to `_()` was unextractable by `pybabel` — **fixed** (named
  placeholder).
- The mobile responsive-table collapse never actually worked (missing `<thead>`/`<tbody>`, a **pre-
  existing Phase 9.5B gap**, only found because this phase did real browser validation) — **fixed**,
  regression-guarded.

## Carried forward from Phase 9.5B (unchanged, still real, not re-litigated)

Onboarding-reissue-for-accepted-account gap, commission-plan reassignment UI, heartbeat rate limiting —
none touched or affected by this phase's work.
