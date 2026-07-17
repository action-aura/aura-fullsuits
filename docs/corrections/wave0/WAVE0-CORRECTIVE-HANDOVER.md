# Wave 0 — Corrective Handover

**Phase**: 3.6, Corrective Wave 0: Stop-Ship Defect Remediation
**Base**: tag `full-product-audit-phase3-5-complete`
**Scope**: `aura-fullsuits` only. `AuraEnterprise` (original repo) was never
read from or written to in this phase.

## What this wave was

The minimum financial/data-safety foundation required before either Retail
or Clinic may enter a controlled paid pilot: the confirmed P0/P1 stop-ship
defects (AUDIT-001, 002, 003, 004, 011, 012, 019) plus directly-dependent
issues found while fixing them (AUDIT-005, 006, 008, 009, 016, 018).

## What this wave explicitly was not

Not Phase 4 Android migration (already complete, not repeated/expanded —
see `cross-platform-financial-validation.md` for why the Android client
itself was left untouched). Not the Owner Control Center, licensing, online
activation, subscription enforcement, customer-specific packaging, remote
telemetry, automatic updates, VPS deployment, Aura Core integration, new
product features, or a UI redesign. None of these were started.

## Commits (9, in order)

| Commit | Summary |
|---|---|
| `7d05a93` | fix: restore retail first-run onboarding (AUDIT-001) |
| `53911a9` | feat: centralize retail authoritative financial calculations (AUDIT-002/003, bundled with the return rewrite and pricing.py Decimal rewrite) |
| `8f315ab` | fix: validate and deduplicate clinic payments, transaction-safe invoicing (AUDIT-011/012/018) |
| `57a3048` | fix: enforce sqlite foreign keys on retail; harden wave0 error handling (AUDIT-016 + info-disclosure/idempotency-scoping fixes found by security review) |
| `4f37e5d` | feat: local offline backup/restore foundation (AUDIT-019) |
| `1b78f72` | test: add Wave 0 focused regression suites (39 Retail + 13 Clinic new tests) |
| `27dc45b` | fix: close auth-bypass and path-traversal gaps in backup/restore routes (found by security review) |
| `9594f78` | docs: Wave 0 correction reports + financial authority contract (Part G) |
| `d77ab97` | docs: cross-platform validation, test report, residual risk register, defect registry status |

## Defects corrected, with evidence

| ID | What | Verified by |
|---|---|---|
| AUDIT-001 | Retail onboarding route never registered | `retail_onboarding_wave0_test.py` (8 tests) |
| AUDIT-002 | Android zero-tax payload persisted as-is | `retail_financial_authority_test.py::test_android_style_zero_tax_payload_still_computes_real_tax` |
| AUDIT-003 | `create_sale()` trusted 100% of client totals | `retail_financial_authority_test.py::test_server_ignores_manipulated_totals_and_unit_price` |
| AUDIT-004 | Returns had no linkage/quantity/duplicate validation | `retail_returns_wave0_test.py` (10 tests) |
| AUDIT-005 | Discount not clamped | `retail_financial_authority_test.py` (2 clamp tests) |
| AUDIT-006 | Binary-float rounding, not Decimal/half-up | `core/retail/pricing.py` rewrite; 26/26 `retail_pricing_test.py` unchanged pass |
| AUDIT-008 | Negative/invalid quantity accepted | `retail_financial_authority_test.py::test_zero_and_negative_quantity_rejected` |
| AUDIT-009 | No oversell guard | `retail_financial_authority_test.py::test_insufficient_stock_rejected_and_does_not_touch_inventory` |
| AUDIT-011 | Clinic payments had no amount validation | `clinic_payment_wave0_test.py` (worked-example table) |
| AUDIT-012 | Clinic payments had no idempotency | `clinic_payment_wave0_test.py::test_duplicate_idempotency_key_returns_original_payment_only` |
| AUDIT-016 | Retail SQLite FKs never enforced | direct `PRAGMA foreign_keys` + `IntegrityError` reproduction |
| AUDIT-018 | Clinic invoice/payment routes had no transaction wrapper | `clinic_payment_wave0_test.py::test_invoice_creation_is_atomic_no_header_without_lines` |
| AUDIT-019 | No backup/restore capability existed | `retail_backup_restore_test.py` (12) + `clinic_backup_restore_test.py` (4) |

Full narrative for each (root cause, exact fix, worked examples, commit,
residual risk) is in the sibling files in `docs/corrections/wave0/`.

## AUDIT-010 decision

**Option B — deferred**, tracked in `wave0-residual-risk-register.md`. The
isolated-per-file `pytest` strategy remains the trusted execution method;
it is 100% reliable (263/263 passing) and this wave did not attempt a
broader test-infrastructure refactor for a P3, non-blocking issue.

## Test results

**263 / 263 tests passing** (155 Retail: 116 baseline + 39 new; 108 Clinic:
95 baseline + 13 new), all run isolated-per-file. Zero unexplained
regressions. The only pre-existing assertions changed are 5 documented,
intentional updates in `retail_pricing_test.py` for the tax-inclusive
refund semantics correction (AUDIT-004) — no test was deleted. Full
breakdown in `wave0-test-report.md`.

Windows packaged smoke test: **re-run as a same-day follow-up** (2026-07-17,
Wave 0 integration addendum) — both products rebuilt and smoke-tested as
real packaged executables, every Wave 0 fix confirmed present in the
artifact, one new non-financial defect found (launcher readiness watchdog
can kill a working server) and tracked, not fixed. See
`docs/build/wave0-windows-packaged-smoke-test.md`. Android build/contract
check: **not applicable** — no Android source was modified.

## Security review findings (both addressed same-day, before this handover)

1. `create_invoice()`/`record_payment()` (Clinic) echoed raw exception text
   to the client — fixed to log server-side, return a generic message.
2. Two new idempotency-key lookups (`create_return()` Retail,
   `record_payment()` Clinic) were unscoped by `company_id`, allowing a
   cross-tenant read of another company's id/status via a guessed key —
   fixed to filter by `company_id`.
3. The new backup/restore routes carried a dead `session.get('is_demo_mode')`
   auth-bypass pattern copied from elsewhere — removed for this
   destructive-operation blueprint.
4. The new backup/restore multipart upload interpolated the
   attacker-controlled `upload.filename` unsanitized into a server-side
   save path (path traversal) — fixed with `werkzeug.secure_filename()`,
   with a regression test.

## Honesty statement (required)

**Neither Retail nor Clinic is being called commercially ready, production
ready, enterprise grade, or safe for paid customers as a result of this
wave.** This wave closed the named stop-ship financial/data-safety gaps
only. The Phase 3.5 audit's release gates were **not rerun** in this wave —
`docs/audit/25-release-gates.md`'s Gate 3 (paid SMB) still names AUDIT-022
and AUDIT-023 (unsigned Windows/Android builds) as open release blockers,
neither of which this wave touched. A real release-readiness determination
requires: rerunning the full release-gate evaluation, the Windows packaged
smoke test, and resolving the remaining P0/P1/P2 items still OPEN in the
master defect registry (see `26-corrective-roadmap.md` for the Wave 1+
sequencing).

## Tag

`corrective-wave0-stop-ship-complete` — created after this document, once
all conditions were verified: no target P0 remains open, no financial P1
remains open, no data-integrity P1 remains open, backup/restore tests pass
(16/16), and no previously-valid test regressed unexpectedly (263/263,
5 changes all documented and intentional).

## Stop condition

Per explicit instruction, this phase stops here. Phase 4 (already complete)
was not repeated or expanded, and Wave 1 was not started.
