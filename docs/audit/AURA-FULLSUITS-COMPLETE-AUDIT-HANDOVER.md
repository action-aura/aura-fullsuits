# Aura FullSuits — Complete Cross-Platform Product Audit (Phase 3.5) — Master Handover

Analysis-only phase. No production source code was modified to produce this
audit. All 29 report documents referenced below exist in `docs/audit/`.

## 1. Executive summary

Two commercial products were audited across two platforms each (Windows
desktop, Android) — Aura Retail and Aura Clinic. **Neither product is
currently safe to sell to a paying customer.** Aura Retail cannot even be
used by a real customer today (no self-service way to create the first user
account exists on either platform), and its Android POS silently charges
$0.00 tax and applies no discount on every sale. Aura Clinic is materially
more mature — real onboarding, real end-to-end reliability proof, a
better-designed financial architecture — but has unresolved payment-
validation gaps and, like Retail, has no backup/restore capability at all.
29 distinct, evidence-based defects were found and are catalogued in the
master defect registry, none of them speculative.

## 2. Audit scope

Analysis and verification only — no fixes, refactors, or feature work were
performed. Both products (Retail, Clinic), both platforms (Windows, Android)
per product, the shared `commercial_runtime` layer, financial correctness,
data integrity, security, Clinic-specific privacy, functional completeness,
commercial readiness, a reduced-scale performance check, backup/recovery,
and cross-version (original-vs-extracted, Windows-vs-Android) parity. Full
scope detail: `00-audit-scope-and-inventory.md`.

## 3. Repository/version state

`aura-fullsuits` (the audited repo): branch `master`, clean at audit start,
23 pre-audit commits (Retail Phase 1-2 + Clinic Phase 3, both 2026-07-12;
Android Phase 4, 2026-07-16), tags `retail-extraction-phase2-complete`,
`clinic-extraction-phase3-complete`, `android-migration-phase4-complete`.
Original repo (`AuraEnterprise`, read-only, untouched throughout this audit):
branch `feat/crm-enterprise-lead-management`, HEAD `414e6ea5`, with unrelated
uncommitted CRM work — no exact extraction-point commit was recorded by
prior phases, a documented limitation, not a defect of this audit. Full
detail: `01-version-and-build-matrix.md`.

## 4. Products and platforms found

Both products exist, and are real (not fabricated), on both platforms:
Retail Windows (`AuraRetail.exe`, real smoke-tested), Retail Android (real
Gradle builds, never device-tested), Clinic Windows (`AuraClinic.exe`, real
13-step smoke-tested), Clinic Android (real Gradle builds, never
device-tested). No `NOT PRESENT IN SOURCE` classification was needed for any
product/platform combination in scope.

## 5. Tests executed

All existing automated tests were actually run in this environment, not
assumed. Retail: 4 test files, **116/116 pass** when run in isolation per
file (0 unit tests exist for Android — `NO-SOURCE`, honestly reported).
Clinic: 6 test files, **95/95 pass** in isolation. **A real, reproduced,
root-caused test-infrastructure defect was found**: running each product's
test files together in one `pytest` process causes 38 (Retail) / 64 (Clinic)
spurious failures due to a module-level path-caching bug in
`commercial_runtime/identity/registry_db.py` — not a production defect (real
deployments never trigger it), but it means the combined `pytest` command is
not currently a trustworthy regression check. Full detail:
`02-test-coverage-and-evidence.md`.

## 6. Confirmed defects count

**29** distinct, evidence-based issues, every one marked PROVEN (one item,
AUDIT-026, splits a PROVEN schema fact from a HIGH-CONFIDENCE INFERENCE about
consequence at unmeasured scale — clearly labeled as such). Zero speculative
findings were included. Full registry: `22-master-defect-registry.{md,csv,json}`.

## 7. Defects by severity

P0: 3 · P1: 4 · P2: 11 · P3: 6 · P4: 5.

## 8. Retail financial verdict

**Broken.** The known tax-on-discount formula question is **DISPROVEN** as a
live defect — `core/retail/pricing.py` is correct and defaults to
after-discount taxation. But `create_sale()` never calls that module and
trusts 100% of client-submitted financial totals (AUDIT-003), which means the
formula's correctness is moot: Windows' web JS frontend happens to compute
correctly, but **Android's POS omits tax and discount on every single sale**
(AUDIT-002, the single most damaging finding in this audit). Returns have no
validation against the original sale at all — repeatable, unlimited,
self-service refund creation (AUDIT-004). Full detail:
`03-retail-financial-audit.md`.

## 9. Clinic financial verdict

**Materially better, still not safe.** Invoice creation is genuinely
well-designed — server-side computed, discount-clamped, tax-after-discount by
deliberate design (matching Retail's intended default). Payment recording,
however, has no amount validation (negative/zero/overpayment all accepted)
and no idempotency protection (a network retry or double-tap duplicates a
payment). Full detail: `04-clinic-financial-audit.md`.

## 10. Windows verdict

Both Windows builds compile, package, and pass their respective real smoke
tests (Retail: 7-step + extras; Clinic: 13-step, the most thorough
verification in this audit). Both are unsigned with no installer wrapper —
a known, pre-existing gap. Retail Windows is unusable by a real customer
regardless of build quality, due to the missing onboarding path. Full detail:
`14-windows-product-audit.md`.

## 11. Android verdict

Both Android apps build successfully (debug/staging/release, all three
variants, both products) with 0 lint errors. **Zero runtime verification
exists for either app** — no emulator or physical device has been available
at any point across Phase 4 or this audit; every runtime claim in this audit
is BUILD ONLY or SOURCE REVIEW ONLY, never TESTED. Retail Android additionally
carries this audit's most severe finding (AUDIT-002). Full detail:
`15-android-product-audit.md`.

## 12. Security verdict

Strong fundamentals across both products: PBKDF2-HMAC-SHA256 password hashing
(600k iterations), correct account lockout, cryptographically random
per-install secret keys, no demo/backdoor credentials, no LAN-exposed
binding, no exploitable SQL injection. The historical Clinic cross-tenant
IDOR fix (8 routes, commit `57e74a0`) is confirmed intact and unmodified.
The financial-trust gaps (AUDIT-003/011/012) are best understood as a
security-adjacent design flaw (insufficient server-side input validation for
money), not a classic auth/injection vulnerability. Full detail:
`08-security-audit.md`, `09-windows-security-audit.md`,
`10-android-security-audit.md`.

## 13. Clinic privacy verdict

One new, real finding not previously documented: `GET /patients/<id>` has no
role gate, so a Secretary (or any non-doctor role) can read the same full
clinical visit history a Doctor can — the role model restricts writes, not
reads. No PII was found leaking into logs (one narrow, already-fixed
exception-handling case reviewed and confirmed safe). Neither Android app
sets `FLAG_SECURE`, a new finding with real (if unverified-in-practice)
screenshot/recent-apps exposure implications for patient data. Full detail:
`11-clinic-privacy-and-sensitive-data-audit.md`.

## 14. Data-integrity verdict

Retail never enables `PRAGMA foreign_keys=ON` (Clinic does, correctly) — every
declared FK constraint in Retail's schema is decorative. Neither product's
two money-writing routes wrap every multi-statement write in an explicit
transaction in every case. No migration/schema-versioning framework exists in
either product (additive-only, try/except-swallowed `ALTER TABLE` calls).
Tenant isolation is otherwise clean in both products. Full detail:
`06-retail-data-integrity.md`, `07-clinic-data-integrity.md`.

## 15. Backup/restore verdict

**None exists, for either product, on either platform.** Confirmed by a
repo-wide code search, not assumed. An Android Settings row literally labeled
"Backup & restore" opens a non-functional "coming soon" placeholder — an
active, misleading commercial-trust issue on top of the underlying gap. Full
detail: `18-backup-and-recovery-audit.md`.

## 16. Performance verdict

**Unverified at the scale the audit spec actually requires.** A real,
reduced-scale test (5,000 products / 3,000 sales / 7,400 line items) was run
in this environment and showed no problems at that scale. A real, schema-
evidenced structural risk was found: neither product indexes the columns most
likely to matter at real commercial scale (`products.sku` for barcode
lookups, `clinic_patients.name` for search). No 10,000-100,000-row test was
performed. Full detail: `17-performance-and-scale-audit.md`.

## 17. Cross-platform parity verdict

**Retail: proven broken.** Windows and Android produce different financial
output for the same input whenever tax or discount is involved, because
Retail's architecture computes financials client-side and Android's client
was never given the correct logic. **Clinic: proven consistent by
construction** — its server-side-computed financial architecture makes
platform divergence structurally impossible for the workflows examined. Full
detail: `05-cross-platform-financial-parity.md`, `19`, `20`.

## 18. Commercial-readiness verdict

Neither product is commercially ready. Retail cannot onboard a real customer
at all. Clinic could plausibly begin a supervised pilot once its payment-
validation gaps are fixed, but neither product has backup/restore, signed
builds, an installer, or any customer-facing documentation/support process.
Full detail: `16-commercial-readiness-audit.md`.

## 19. Enterprise-grade verdict

**Neither product is enterprise-grade, and neither is close.** Every
product/platform combination fails Gate 4 outright, and — more importantly —
every one fails Gate 2 (controlled pilot) today. Scored enterprise-readiness:
Retail Windows 10/100, Retail Android 5/100, Retail overall 8/100, Clinic
Windows 28/100, Clinic Android 18/100, Clinic overall 22/100. Full detail:
`23-enterprise-grade-scorecard.md`, `24-product-classification-and-verdict.md`.

## 20. Stop-ship defects

AUDIT-001 (Retail: no onboarding path exists at all), AUDIT-002 (Retail
Android: $0 tax/discount on every sale), AUDIT-003 (Retail: server trusts
100% of client-submitted financial totals), AUDIT-004 (Retail: returns have
no validation against the original sale — repeatable refund abuse),
AUDIT-011/012 (Clinic: payment recording has no amount validation or
idempotency protection), AUDIT-019 (both: no backup/restore capability
exists at all).

## 21. First-sale blockers

Everything in item 20, plus: AUDIT-005/006/007/008/009 (Retail, bundled with
the AUDIT-003 fix), AUDIT-013/018/020 (Clinic), AUDIT-016/017 (Retail data
integrity), AUDIT-022/023 (signing/installer, both products), and a genuine
device/emulator testing pass for both Android apps (not yet performed at any
phase). Full detail: `27-must-fix-before-first-sale.md`.

## 22. Deferred improvements

Test-infrastructure fix (AUDIT-010), missing invoice states (AUDIT-015),
`FLAG_SECURE` (AUDIT-021), missing indexes (AUDIT-026, though this should not
be deferred past small-scale customers), the misleading backup UI placeholder
(AUDIT-027, resolves alongside AUDIT-019), dead code cleanup (AUDIT-014),
version-number sync (AUDIT-025), and all of Wave 3 (fine-grained RBAC,
observability, controlled updates, formal DR drills, accessibility,
licensing enforcement). Full detail: `28-safe-to-defer-until-after-revenue.md`.

## 23. Recommended corrective order

Wave 0 (stop-ship, item 20) → Wave 1 (first-sale blockers, item 21) → Wave 2
(commercial quality, including the Android device-testing pass) → Wave 3
(enterprise maturity). Full detail: `26-corrective-roadmap.md`.

## 24. Recommended next development phase

A corrective phase addressing Wave 0 in full, starting with **AUDIT-001**
(plausibly the cheapest fix in this entire audit relative to its severity —
likely a two-line blueprint-registration fix, per `21`'s analysis) and
**AUDIT-003** (whose fix resolves AUDIT-002 as a side effect). Recommend
targeting **Aura Clinic** as the first product brought to a real paid pilot,
given it has no P0 issues and a materially stronger foundation across nearly
every dimension scored in `23`. Do not begin Owner Control Center, licensing
enforcement, or any of the other explicitly-excluded work until Wave 1 is
complete for whichever product is sold first.

## 25. Exact limitations of this audit

- No physical Android device or emulator was available — every Android
  runtime claim in this audit is BUILD ONLY or SOURCE REVIEW ONLY.
- No printer or physical barcode scanner was available.
- No true 10,000-100,000-row performance test was run (a ~5,000-row test was
  run instead; see item 16).
- No exact original-repository commit reference exists for a byte-precise
  original-vs-extracted diff; `21`'s original-vs-extracted analysis relies on
  prior phases' own extraction reports rather than a fresh diff.
- Not every single route in `retail_api.py`/`clinic_api.py` was individually
  re-audited line-by-line for authorization decorators — a representative,
  financially/tenant-critical sample was used (`08`).
- No formal penetration test, threat model, or legal/regulatory compliance
  determination (HIPAA/GDPR/etc.) was performed or claimed.
- One dispatched research subagent (security evidence-gathering) failed
  partway through due to an external session-limit condition; its scope was
  covered directly by the primary audit process instead, using the same
  evidence standard, so no coverage gap resulted, but it's noted for
  transparency.

## 26. Exact tools and environments used

Windows 11 Pro (10.0.26200), Python 3.11.9 (`AuraEnterprise/.venv`), pytest
9.1.1, Java 17.0.19, Android SDK platform 34 / build-tools 34.0.0, Gradle 8.9,
`git`, `grep`/`Grep` tool, direct source reading, and one ad hoc Python script
(`.audit-temp/build_registry.py`, not committed — generates the defect
registry's three output formats from one source list) plus one ad hoc
reduced-scale SQLite performance script (run and its temp database deleted
immediately after, per this audit's own data-handling rules).

## 27. Paths to every report

```
docs/audit/00-audit-scope-and-inventory.md
docs/audit/01-version-and-build-matrix.md
docs/audit/02-test-coverage-and-evidence.md
docs/audit/03-retail-financial-audit.md
docs/audit/04-clinic-financial-audit.md
docs/audit/05-cross-platform-financial-parity.md
docs/audit/06-retail-data-integrity.md
docs/audit/07-clinic-data-integrity.md
docs/audit/08-security-audit.md
docs/audit/09-windows-security-audit.md
docs/audit/10-android-security-audit.md
docs/audit/11-clinic-privacy-and-sensitive-data-audit.md
docs/audit/12-retail-functional-audit.md
docs/audit/13-clinic-functional-audit.md
docs/audit/14-windows-product-audit.md
docs/audit/15-android-product-audit.md
docs/audit/16-commercial-readiness-audit.md
docs/audit/17-performance-and-scale-audit.md
docs/audit/18-backup-and-recovery-audit.md
docs/audit/19-retail-windows-vs-android-parity.md
docs/audit/20-clinic-windows-vs-android-parity.md
docs/audit/21-original-vs-extracted-parity.md
docs/audit/22-master-defect-registry.md
docs/audit/22-master-defect-registry.csv
docs/audit/22-master-defect-registry.json
docs/audit/23-enterprise-grade-scorecard.md
docs/audit/24-product-classification-and-verdict.md
docs/audit/25-release-gates.md
docs/audit/26-corrective-roadmap.md
docs/audit/27-must-fix-before-first-sale.md
docs/audit/28-safe-to-defer-until-after-revenue.md
docs/audit/AURA-FULLSUITS-COMPLETE-AUDIT-HANDOVER.md   (this document)
```
