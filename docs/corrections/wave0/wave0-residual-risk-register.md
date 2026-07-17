# Wave 0 — Residual Risk Register

Every item below is a known, deliberately-not-fixed-in-this-wave gap.
Nothing here is hidden or implied to be resolved.

## AUDIT-010 — Cross-file pytest pollution: decision recorded

**Decision: Option B — defer, keep the isolated-per-file execution strategy,
track separately for a future wave.**

Rationale: AUDIT-010 is rated P3 in the master defect registry, not a
release blocker or enterprise blocker, with "None" recorded for customer
impact, financial impact, privacy impact, data-integrity impact, and
security impact — it affects developer/CI confidence in a combined test
run, not the shipped product (real deployments never run multiple products'
test suites inside one Python process). Root cause is
`commercial_runtime/identity/registry_db.py`'s `DB_PATH`, a module-level
constant frozen at first import, which ignores a later test file's
`AURA_APP_DATA` change within the same combined pytest process — the same
class of issue likely also affects `database/schema.py`'s `BASE_DIR`/
`SUBSYS_DIR` module-level resolution in both products, and now touches more
test files than when it was first found (this wave added 6 more). A correct
fix means making database-path resolution dynamic per-call across three
shared/product modules and re-verifying every existing test still isolates
correctly — a broader test-infrastructure refactor than "small and clearly
understood," which the spec explicitly gates Option A on. The isolated
-per-file strategy is confirmed still 100% reliable: all 263 Wave 0 tests
(155 Retail + 108 Clinic) pass with zero failures when run one file at a
time (see `wave0-test-report.md`), so there is no coverage gap today, only
an ergonomics gap in how the suite is invoked.

## `sales.sale_number` bare-UNIQUE-vs-per-company-sequence collision risk

Identical latent flaw to the one fixed for `returns.return_number` in this
wave (AUDIT-004 correction): `sales.sale_number` has a bare, non-company
-scoped `UNIQUE` constraint, but `_next_ref()`'s counter resets per company,
so two different companies' first sale could both generate `"SALE-000001"`
and collide in this shared multi-tenant database. Not fixed in this wave
(out of the named AUDIT-001/002/003/004/011/012/019 scope) — currently
masked in every test helper by an explicit random-seed workaround
(`doc_sequences` reseeded to a random starting value per test company).
**This is a real production risk**, not just a test artifact: two genuine
customer companies onboarding around the same time could hit this
collision in the field. Recommended for the next wave.

## No customer-credit ledger (Retail non-AR paths, Clinic)

Both products now reject any payment/return amount that would create
apparent "credit" beyond what's explicitly modeled (Retail's existing
Accounts Receivable credit-sale system is unaffected by this wave; Clinic
has no equivalent ledger at all). This is documented, deliberate behavior,
not a bug — but it means legitimate future business needs (e.g. clinic
patient credit accounts, retail walk-in partial-payment tracking outside
AR) are unsupported today and were not designed for in this wave.

## Windows packaged smoke test — now closed, one new defect found

**Update (2026-07-17, Wave 0 integration addendum)**: both products were
rebuilt (`docs/build/wave0-windows-packaged-smoke-test.md`) and
smoke-tested as packaged executables. Every Wave 0 financial/data-safety
fix was confirmed present and working in the actual packaged artifact
(Android-style zero-tax payload correctly taxed, Clinic overpayment
rejected, Clinic duplicate payment deduplicated, onboarding works, backup
creation works, no DB corruption after a hard process kill).

**Update (2026-07-17, Phase 3.7 — CLOSED)**: this defect was assigned
`AUDIT-030`, confirmed release-blocking, root-caused with direct evidence,
fixed, and verified with a real 10+ minute packaged long-run smoke test
for both products. See `docs/corrections/launcher/` (root cause: the
readiness check polled the bare `/` path, which no route has ever served —
not a proxy issue, disproven with logged evidence) and
`docs/audit/22-master-defect-registry.*` (AUDIT-030). No longer an open
residual risk.

## Android client not touched

The Android zero-tax defect's *financial* impact is neutralized by the
backend fix (see `cross-platform-financial-validation.md`), but the Android
Kotlin source still contains the underlying client bug (always submitting
`tax_rate: 0, discount_pct: 0`). It was deliberately left alone per the
explicit Wave 0 scope boundary against Android migration/UI work. A future
wave should still fix the client for its own sake (correct UI display of
tax/discount to the cashier, not just correct server-side billing).

## Backup/restore: no scheduler, no cross-process lock, manual `SCHEMA_VERSION`

Documented in full in `backup-and-restore-foundation.md`'s own residual
-risk section: no automatic scheduling (by design), no enforcement that the
app is stopped during a restore (relies on operator discipline / product
documentation), and `SCHEMA_VERSION` is a manually-maintained constant that
must be remembered on a future breaking schema change.

## Employee invite/setup and admin-management routes not re-audited

`onboarding_routes.py`'s `employee_setup()`, `create_employee()`, and the
various `/api/admin/*` routes were read in full while fixing `create_admin()`
but were not independently re-audited for financial or security defects in
this wave — none were named in the Wave 0 target list, and none showed up
as a *dependency* of fixing `create_admin()` (see the onboarding correction
doc's scope note).

## `IS_DEMO_MODE`'s `session['is_demo_mode']` flag is dead but present

Found while security-reviewing the new backup/restore routes: `mt_auth.py`
and `import_api.py` both contain a `session.get('is_demo_mode')` bypass
pattern that nothing in the current codebase ever actually sets (real demo
-mode gating goes through `commercial_runtime/security/modes.py`'s
config/env-based `retail_demo_mode_enabled()`/`clinic_demo_mode_enabled()`
instead). It was excluded from the new backup/restore routes on purpose
(see the security-fix commit), but the dead flag itself was left in place
in `mt_auth.py`/`import_api.py` — removing it is a cleanup, not a Wave 0
stop-ship fix, since it is not currently reachable/exploitable anywhere it
still appears. Recommended cleanup for a future wave to avoid it being
copy-pasted into a genuinely dangerous context again.
