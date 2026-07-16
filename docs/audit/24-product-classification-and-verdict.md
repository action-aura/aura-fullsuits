# Product Classification and Verdict

## Classification method

Raw dimension averages (from `23`) are reported for transparency, but **the
final classification is capped by unresolved blockers, not just the average**
— consistent with the audit's own instruction that "a product must not be
classified as enterprise-grade merely because its overall average is high."
This audit extends the same principle symmetrically: a product is not
classified into a *higher* band than its worst unresolved blocker justifies,
regardless of how healthy its average looks. Where the raw average and the
blocker-capped classification disagree, both are shown and the disagreement
is explained.

## 1. Aura Retail — Windows

- **Raw dimension average**: ~43/100 (would land in "Early MVP" by number alone).
- **Blocker-capped classification**: **0-29 — Prototype / unsafe for
  commercial use.**
- **Why the cap applies**: AUDIT-001 (no self-service onboarding at all) means
  a real customer cannot use the product without direct database
  intervention — this fails even Gate 1's spirit of "the application starts
  and can be used," and definitely fails Gate 2/3/4. A P0 that blocks first
  use overrides a merely-adequate average.
- **Strongest areas**: Security fundamentals (password hashing, lockout,
  secret management — all genuinely strong, `08`), offline architecture,
  localization.
- **Weakest areas**: Financial correctness (client-trusted totals, unvalidated
  returns), commercial readiness (no onboarding, no backup, unsigned/no
  installer).
- **Commercial-sale verdict**: **NO.**
- **Pilot-sale verdict**: **NO** — cannot even complete first-run setup.
- **Enterprise-sale verdict**: **NO.**
- **Exact blockers**: AUDIT-001, AUDIT-003, AUDIT-004, AUDIT-019 (minimum).
- **Confidence**: PROVEN for every blocker cited (all directly source-read,
  reproducible).

## 2. Aura Retail — Android

- **Raw dimension average**: ~35/100.
- **Blocker-capped classification**: **0-29 — Prototype / unsafe for
  commercial use.**
- **Why**: Everything Windows has, plus AUDIT-002 (the single worst finding
  in this audit — $0 tax on every sale), plus zero runtime verification ever
  performed (no device/emulator, any phase).
- **Strongest areas**: Build/packaging mechanics (real, working Gradle
  builds), UI code quality (clean Compose, well-structured per Phase 4's own
  migration).
- **Weakest areas**: Financial correctness (worst in the whole audit), test
  maturity (zero tests), reliability (never run).
- **Commercial-sale verdict**: **NO.**
- **Pilot-sale verdict**: **NO.**
- **Enterprise-sale verdict**: **NO.**
- **Exact blockers**: AUDIT-001, AUDIT-002, AUDIT-003, AUDIT-004, AUDIT-019,
  plus "never verified on a device" as an independent blocker in its own
  right.
- **Confidence**: PROVEN for the code-level findings; the *consequence* of
  never running on a device is, by definition, UNVERIFIED rather than PROVEN
  — stated honestly rather than assumed benign.

## 3. Aura Retail — overall product

**Classification: Prototype / unsafe for commercial use (0-29).** Neither
platform is usable by a real customer today. This is not a close call — the
onboarding gap alone is disqualifying independent of every other finding.

## 4. Aura Clinic — Windows

- **Raw dimension average**: ~52/100 (would land in "Beta product /
  controlled pilot only" by number alone).
- **Blocker-capped classification**: **30-49 — Early MVP / internal testing
  only.**
- **Why the cap applies**: The audit's own Gate 2 (controlled pilot)
  explicitly requires "financial P0/P1 issues resolved." AUDIT-011 and
  AUDIT-012 (unvalidated, non-idempotent payment recording) are unresolved
  financial P1s. A product that cannot clear its own stated pilot bar cannot
  be classified as "ready for a controlled pilot" merely because its average
  score happens to land in that numeric range — the average is diluted by
  genuinely strong areas (reliability, security, onboarding) that don't
  compensate for a live financial-integrity gap in the one workflow
  (payments) a pilot would exercise most.
- **Strongest areas of the whole audit, either product**: Reliability (the
  only real, complete, 13-step end-to-end smoke test including restart
  persistence), tenant-isolation security posture (diff-verified IDOR fix
  with regression coverage), onboarding (fully working).
- **Weakest areas**: Payment validation/idempotency, backup/recovery (shared
  gap with Retail), the over-broad clinical read-access finding (AUDIT-020).
- **Commercial-sale verdict**: **NO, not yet** — closer than Retail by a wide
  margin, but the payment-integrity and backup gaps are real blockers for
  money changing hands and patient-data safety respectively.
- **Pilot-sale verdict**: **CONDITIONAL** — could plausibly run a supervised,
  low-stakes pilot (a single trusted clinic, close support-team monitoring)
  if AUDIT-011/012 are fixed first; not recommended as-is.
- **Enterprise-sale verdict**: **NO.**
- **Exact blockers**: AUDIT-011, AUDIT-012, AUDIT-019, AUDIT-020 (privacy).
- **Confidence**: PROVEN for all four.

## 5. Aura Clinic — Android

- **Raw dimension average**: ~41/100.
- **Blocker-capped classification**: **30-49 — Early MVP / internal testing
  only.**
- **Why**: Same financial/backup/privacy blockers as Windows (shared
  backend), plus zero runtime verification (no device).
- **Strongest areas**: Financial architecture is structurally sound (server-
  computed, so Android inherits Clinic's correctness by construction — `05`),
  clean migration (Phase 4).
- **Weakest areas**: Zero device testing, zero automated tests.
- **Commercial-sale verdict**: **NO.**
- **Pilot-sale verdict**: **NO** — cannot recommend a pilot on a build that
  has never been run on real hardware, regardless of how sound the backend
  architecture is.
- **Enterprise-sale verdict**: **NO.**
- **Exact blockers**: AUDIT-011, AUDIT-012, AUDIT-019, AUDIT-020, plus
  "never verified on a device."
- **Confidence**: PROVEN for the code-level findings; device behavior
  UNVERIFIED.

## 6. Aura Clinic — overall product

**Classification: Early MVP / internal testing only (30-49).** This is the
stronger of the two products by a wide margin on almost every dimension this
audit examined — real onboarding, real end-to-end reliability proof, better
financial architecture, better data-integrity posture (FK enforcement) — but
it is not yet safe for a real paid customer, and the gap between "how good
the engineering is" and "is it actually ready" is smaller here than for
Retail, not zero.

## Direct answer: which product is closer to a first paid customer?

**Aura Clinic**, clearly, on the evidence in this audit. It has no P0 issues,
has a genuinely proven end-to-end workflow, and its remaining blockers
(payment validation, backup, one privacy finding) are all individually
small/medium-effort fixes (`26`). Aura Retail's blockers include a P0 that
prevents any use at all and a P0 that is arguably the single most damaging
finding in the whole audit (silent tax omission on every Android sale) — both
are more fundamental and, in the tax case, more consequential once live.
