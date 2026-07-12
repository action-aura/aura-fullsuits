# Extraction Plan — Aura FullSuits

Companion to `source-inventory.md` (what exists, where it goes) and `dependency-map.md` (what depends on what). This document sequences the work.

## Guiding constraints (carried from the task brief, restated for reference during execution)

- No rewrite of Retail or Clinic — extraction, not redesign.
- SOURCE repo (`AuraEnterprise/AuraEnterprise`) is never deleted, moved, or modified by this work.
- Retail and Clinic stay separate products; neither imports the other.
- No premature Aura Core coupling — only `commercial_runtime/` as a small shared layer.
- Financial logic (retail pricing/tax, clinic billing) is copied as-is; any bug fix found along the way gets its own documented, tested commit — never a silent change.
- Deny-by-default security; no shared secrets/tenant IDs/license keys across installs (SOURCE already does this correctly for the app secret key — must not regress it).

## Sequencing

**Phase 0 (this phase) — Discovery.** Done. Produced `source-inventory.md`, `dependency-map.md`, this plan, and `risk-register.md`. No SOURCE changes made.

**Phase 1 — Scaffold `aura-fullsuits/`.** Create the directory tree from the task brief (products/, android/, commercial_runtime/, owner_control_center/, deployment/, scripts/, tests/, docs/). Populate root-level docs (README, LICENSE-POLICY, SECURITY, PRIVACY, CHANGELOG), `.env.example`, `.gitignore`, `pyproject.toml`, `requirements/*.txt` (curated per dependency map §1). Initialize independent git repo only after the tree is populated and Retail (Phase 2) is verified importable — initializing empty first risks a confusing half-built first commit.

**Phase 2 — Extract Retail.** Copy `api/subsystems/retail_api.py`, `core/retail/pricing.py`, `static/js/subsystem-retail.js`, the `init_retail`/`_seed_retail` functions (surgically extracted from `subsystem_db.py`) and the shared connection helpers they need, `tests/retail_pricing_test.py`, `tests/retail_security_test.py`, `docs/retail/RETAIL_SECURITY_PHASE_1.md`. Resolve the three flagged-but-unlocated items (source-inventory #13b/14/15: HID scanner, printing/sharing, import/export) by grepping `RetailScreens.kt`/`RetailExtraScreens.kt` and the web JS before declaring them ported or intentionally omitted. Author `launcher_retail.py` and `aura_retail.spec` (new files, patterned on the existing `launcher_accounting_dev.py`/`aura_accounting_dev.spec` convention — this is the one place Phase 2 authors new files rather than only copying). Produce `retail-extraction-report.md`. Run the ported tests before moving on.

**Phase 3 — Extract Clinic.** Same mechanics as Phase 2, plus: decide and document the resolution for the Accounting cross-import (dependency-map §4 — cut vs. adapter) rather than silently carrying a hard dependency on unrelated Accounting code. Author a new Clinic test suite (none exists in SOURCE — this is new authoring, not extraction, and should be flagged as such in the report, not presented as "ported"). Add the PII-telemetry-rejection tests required by the brief even though no telemetry client exists yet (they'll initially test against `commercial_runtime/telemetry`'s allowlist once Phase 7 exists — sequence accordingly, or stub the allowlist early). Produce `clinic-extraction-report.md`.

**Phase 4 — Android.** Copy the whole `android/` tree once (it's already a single shared project for both flavors — do not fork it into two). Rewrite the Chaquopy staging block in `android/app/build.gradle` to stage only `products/retail/backend` + `products/clinic/backend` + `commercial_runtime` (today it stages the entire enterprise backend into every flavor, unused — dependency-map §1 flags this). Add debug/staging/production build config variants, network security config (HTTPS-only prod, no cleartext), configurable Owner Server base URL. Attempt real Gradle builds; report actual results — do not claim a build succeeded without running it.

**Phase 5 — Commercial identity foundation.** Build `commercial_runtime/identity/`, `installation/`, `licensing_contracts/` per the brief's UUID/contract spec. This is genuinely new code (SOURCE has no equivalent beyond the `license_validator.py` stub, which is not reused as-is — see source-inventory #43).

**Phase 6-14 — Owner Control Center.** New build (backend, DB, dashboard) — nothing in SOURCE to extract here; SOURCE has no owner-facing admin platform, no telemetry, no subscription/payment tracking. This is the largest net-new component of the whole task.

**Phase 15-16 — Deployment + environment separation.** New build, informed by SOURCE's existing `Dockerfile`/`docker-compose.yml` patterns (reuse the gunicorn/waitress serving pattern already proven there) but targeting the new Owner Control Center + Retail/Clinic backends, not the old monolith.

**Phase 17-19 — Testing, parity validation, handover docs.** Final gate before declaring done.

## Execution note on pacing

Phase 0 discovery alone required three parallel research passes and hit an account-level session/usage limit partway through (one subagent was cut off mid-run; its gaps were filled by direct inspection — see source-inventory notes). Phases 6-14 (Owner Control Center: Postgres schema, auth, dashboard, telemetry ingestion, billing/subscriptions) and Phase 4 (real Android Gradle builds) are each independently substantial — this is realistically a multi-session effort, not a single continuous run. Recommend proceeding phase-by-phase with a short checkpoint after each, per the brief's own "report, then continue unless blocked" execution model, rather than attempting Phases 1 through 19 back-to-back.
