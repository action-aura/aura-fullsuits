# Wave 1A — Physical Device Validation: Handover

## Scope
Physical-device validation of Aura Retail and Aura Clinic Android apps on a real Infinix X6528 (Android 13, arm64-v8a), following the Phase 4 Android migration corrective pass. Both apps clean-installed, onboarded, and exercised through their core workflows on real hardware, with 7 real defects found and fixed (MOB-001 through MOB-006 fixed; MOB-007 registered and deferred).

## Device
Infinix X6528 (Transsion/XOS, Android 13, API 33), `device` status confirmed via `adb devices -l` before any work began. Full specs in `device-environment-report.md`.

## Baseline
Tag `android-migration-phase4-corrective-complete` verified matching `HEAD` before any work began; 29/29 Retail + 17/17 Clinic Kotlin tests rerun clean; fresh debug APKs built and checksummed. Details in `pre-device-baseline.md`.

## Defects found and fixed (real device, this wave)
1. **MOB-001** — Retail: taxed cash sales rejected as credit sales (client sent wrong `amount_paid`). Fixed.
2. **MOB-002** — Clinic: embedded backend never started (missing `requests` Chaquopy dependency). Fixed.
3. **MOB-003** — Clinic: payment HTTP rejections showed generic "can't reach server". Fixed + tested.
4. **MOB-004** — Clinic: login HTTP rejections showed generic "can't reach server". Fixed + tested.
5. **MOB-005** — Clinic: malformed free-text appointment dates silently vanished; also fixed single-day-only schedule view. Fixed + tested.
6. **MOB-006** — Retail: Settings crashed (stale nav route). Fixed.
7. **MOB-007** — Clinic: Arabic translation coverage incomplete (RTL mechanism works, most strings untranslated). Registered, deferred to Wave 1B per explicit user decision.

Full detail: `wave1a-mobile-defect-registry.md`.

## Feature gaps found and closed this wave (not pre-planned, discovered live)
- Clinic invoice drill-down (line items + payment history) — backend endpoint existed, app never called it. Added.
- Clinic appointment "upcoming" view (was single-day-only) — added `from_date` backend range query + client UI.

## Parts completed
A (clean install), B/H (onboarding+auth), C/D (financial worked example + idempotency), E (returns), F (barcode/camera), G (Retail backup/restore), I (Clinic core workflow), J (payment errors), K (role navigation infra), L (Clinic backup/restore), M (FLAG_SECURE), N (logcat privacy), O (language/RTL), P (lifecycle/resilience), Q (upgrade/data-preservation, via incidental evidence), R (performance), S (regression), T (release artifacts + checksums), U (this documentation set).

## Excluded, as instructed
Production signing, installer/distribution workflow, Windows AUDIT-022/023, Owner Control Center, licensing/subscription enforcement, VPS deployment, telemetry, automatic updates, Jordan e-invoicing, competitor-parity features, Aura Core integration. Also: Clinic full Arabic translation (MOB-007) explicitly deferred to Wave 1B by the user, not attempted here.

## Residual risks
See `wave1a-mobile-residual-risk-register.md` — the Clinic pytest cross-file isolation issue (R-2, proven pre-existing and unrelated to this wave's change), MOB-007's deferred scope (R-1), device-specific background throttling behavior (R-3), and the upgrade-test methodology note (R-6).

## What is explicitly NOT claimed
Neither product is described as "commercially ready," "production ready," "enterprise grade," "production signed," or "safe for paid customers." This wave validates real-device functional correctness and fixes real defects found; it does not constitute a release-readiness gate.

## Tag
`android-wave1a-physical-device-validated` applied only after all Definition-of-Done conditions in the original spec were genuinely met: physical device used throughout, both apps launch, both embedded backends run on-device, Retail financial test passes, Clinic payment validation passes, CameraX/ML Kit tested physically, app data survives restarts (verified ~10 times), no unresolved critical privacy leak, backup/restore does not corrupt data, no unexplained P0/P1 remains (all found P0/P1-class issues — MOB-002, MOB-006 crash — were fixed and reverified), and no report in this set falsely claims an untested item as tested.
