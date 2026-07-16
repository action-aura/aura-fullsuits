# Original vs. Extracted Parity

## Important caveat (see `00`)

No exact original-repo commit SHA was recorded at the time Retail (Phase 1/2)
or Clinic (Phase 3) were extracted. The original repo has since advanced
(unrelated CRM feature work, currently uncommitted) and cannot be rolled back
to inspect for this audit (read-only constraint). This document therefore
relies on the extraction reports written *at the time of extraction*
(`docs/migration/retail-extraction-report.md`,
`docs/migration/clinic-extraction-report.md`) as the record of what changed,
rather than a fresh line-by-line diff against the original repo's current
state (which would conflate genuine extraction-time changes with four days of
unrelated original-repo development).

## Aura Retail

| Area | Status | Notes |
|---|---|---|
| Core POS/pricing business logic | COMPLETE AND CONSISTENT (copied byte-identical, adapted imports only) | Per the extraction report's own "Copied files (byte-identical business logic, adapted imports only)" section |
| Password hashing / security posture | **EXTRACTED ONLY IS BETTER** | Original had a documented backdoor and SHA-256 (unsalted) hashing (per this session's own prior-phase memory and `commercial_runtime/security/passwords.py`'s docstring, "Extracted verbatim from Action Aura Enterprise's core/security/passwords.py (the Retail Phase 1 security remediation)"); extracted version has PBKDF2-HMAC-SHA256, no backdoor, confirmed by this audit (`08`) |
| Per-install secret key | **EXTRACTED ONLY IS BETTER** | Same Phase 1 remediation — `commercial_runtime/security/app_secret.py`, confirmed correct by this audit (`08`) |
| Onboarding/first-run | **ORIGINAL likely had one** (the monolith's shared `onboarding_routes.py` module still exists and is used by Clinic) **but the EXTRACTED Retail product lost it** — `products/retail/backend/app.py` never registers it | **This is a genuine extraction regression, not an original-source gap** — the original monolith almost certainly had a working onboarding path (since the shared blueprint that provides it still exists in `commercial_runtime` and IS correctly wired for Clinic), and it was simply never wired into Retail's standalone `app.py` during Phase 2 extraction. This reframes the `12` finding: it is not "a feature that never existed," it is **an extraction defect**. |
| Export | NOT PRESENT ORIGINAL, NOT PRESENT EXTRACTED | Already correctly identified and preserved as absent per prior-phase documentation and re-confirmed by this audit (`03`/`12`) — not a regression, a faithful non-feature |
| Tax/discount server-side enforcement | Not confirmed either way for the original monolith in this pass (no commit reference to inspect) — `core/retail/pricing.py`'s docstring claims to be ported from the same monolith's pricing logic, so the *formula* likely existed originally; whether the original's sale-creation route called into it (unlike the extracted version, which doesn't) is UNVERIFIED | Flagged rather than asserted — the extracted version's gap (`03`) may or may not be a regression; not enough evidence to say either way without the original commit |
| Windows packaging | ORIGINAL ONLY had a shared multi-subsystem launcher; EXTRACTED has a dedicated `launcher_retail.py`/`aura_retail.spec`, new files, not a straight extraction | Intentional — required for a standalone product; found a real bug (`sys._MEIPASS` static-asset path) during Phase 2B validation that the shared original launcher likely didn't have (or had already solved generically) — fixed in the extracted version |
| Android | ORIGINAL had one shared Gradle project (two flavors); EXTRACTED has two fully independent Gradle projects (Phase 4) | Intentional, per Phase 4's explicit task scope — deliberate architectural improvement, not a straight port |

## Aura Clinic

| Area | Status | Notes |
|---|---|---|
| Core clinical/billing business logic | COMPLETE AND CONSISTENT (per `clinic-extraction-report.md`'s own copied-files record) | |
| Onboarding | COMPLETE AND CONSISTENT — correctly extracted and wired, confirmed working by real smoke test | |
| Cross-tenant isolation | **EXTRACTED ONLY IS BETTER, after an interim regression that was caught and fixed within the same phase** — the 8-route IDOR (`57e74a0`) was introduced (or exposed) during extraction and fixed before Phase 3 completed, with regression tests added; the *original* monolith's exposure to the same bug class was not independently re-verified in this pass (no commit reference) | Given the git-history evidence (`07`), this is best read as "a bug that existed transiently in the extracted product's own commit history, caught by the extracting team's own review, and fixed" — not evidence about the original's own state either way |
| Prescription JSON encoding bug (`str(list)` repr vs. real JSON) | **ORIGINAL HAD THE BUG, EXTRACTED FIXED IT** | Documented explicitly in `clinic_api.py`'s own comment (Phase 3 fix) — a genuine, real improvement over the original, confirmed by this audit (`13`) |
| Accounting cross-subsystem mirror | **ORIGINAL HAD real cross-subsystem integration; EXTRACTED has a dead, always-failing, silently-swallowed stub** of the same code | Not itself a functional regression for Clinic's own data (the invoice is still written correctly), but the code's own comment describes behavior that can no longer occur — a genuine "leftover from extraction" finding (`04`) |
| Windows packaging | New files (`launcher_clinic.py`/`aura_clinic.spec`), patterned on Retail's already-validated equivalents; no packaging bug found (benefited from Retail's Phase 2B fix being applied from day one) | Intentional, and arguably executed *better* than Retail's own first attempt, since it inherited the fix rather than rediscovering the bug |
| Android | Same situation as Retail — two independent projects vs. the original's one shared project | Intentional, Phase 4 |

## Overall verdict

Both extractions preserved or improved the original's core business logic and
materially improved security posture (password hashing, secret management,
tenant isolation once the transient Clinic IDOR was fixed). The one clear,
concrete **extraction regression** found by this audit is Retail's missing
onboarding wiring (`12`) — the underlying capability exists in
`commercial_runtime` and is correctly used by Clinic, it was simply never
connected to Retail's own `app.py`. This is very likely the fastest, highest-
leverage fix available across this entire audit: it may be as small as adding
the same two lines Clinic already has.
