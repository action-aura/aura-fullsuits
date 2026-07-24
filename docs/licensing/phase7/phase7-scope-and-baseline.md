# Phase 7 -- Scope and Baseline

## Governing checkpoint

Verified before any change: `HEAD` at `ff52fca` ("docs: complete phase 6 licensing activation handover"), working tree clean (`git status --porcelain` empty), all 12 required checkpoint tags present and unmoved:

```
android-migration-phase4-complete
android-migration-phase4-corrective-complete
android-wave1a-physical-device-validated
aura-owner-foundation-phase5-complete
aura-owner-licensing-activation-phase6-complete
clinic-extraction-phase3-complete
commercial-packaging-wave1b-complete
commercial-release-gates-wave1c-complete
corrective-wave0-stop-ship-complete
full-product-audit-phase3-5-complete
retail-extraction-phase2-complete
windows-launcher-watchdog-corrected
```

## Governing spec was truncated

The Phase 7 instruction message was cut off mid-"Part AF -- Testing Requirements" at the 50,000-character limit (Android Retail unit-test bullet list). Parts A through AE, and the opening of AF, are complete and unambiguous and are treated as binding as written. Whatever followed AF in the original spec -- the remainder of the testing enumeration, and (by the established Phase 5/6 pattern) an evidence/documentation part, a Definition of Done, a Final Response Format, and the exact final tag name -- was not received. Handling: the fully-specified parts (A-AE) are executed exactly as written; the missing tail is inferred conservatively from the Phase 6 precedent (46-item structured final response; tag name follows the `aura-<domain>-<phase>-complete` convention, inferred here as `aura-product-licensing-integration-phase7-complete` unless the user supplies the real string) and every such inference is called out explicitly at the point it's used, the same way Phase 6's truncated tag name was handled.

## Repository layout differs from the spec's assumed paths -- corrected

The spec assumed `products/retail`, `products/clinic`, `android/aura-retail`, `android/aura-clinic`. All four exist exactly at those paths (an initial glob with a trailing-slash pattern returned nothing and was a tooling artifact, not a real absence -- corrected on retry). No path correction was actually needed; this is recorded because the first check appeared to contradict the spec and warranted verification before proceeding.

## Unexpected directories found and resolved

Discovery of the repo tree surfaced three directories not mentioned in any prior checkpoint or memory:

- **`commercial_runtime/`** -- live, actively imported by both product backends (`app.py`, `launcher_*.py`, `config.py`, `database/schema.py`, every backend test file), and even reused by Owner (`owner/app/models/__init__.py`, `owner/app/security/passwords.py`, `owner/app/system/backup.py`, the activation simulator). This is the real shared Python library underneath Retail and Clinic. Last touched 2026-07-20 (Wave 1B, schema-migration-safety work). Not stale -- this is where Phase 7's Python-side shared licensing domain belongs.
- **`commercial_runtime/licensing_contracts/`** -- exists as a subpackage but is empty (0 files). An already-scaffolded, never-populated placeholder. This is the designated home for the Part C shared licensing domain classes on the Python/Windows side.
- **`commercial_runtime/telemetry/`** and **`commercial_runtime/updates/`** -- also empty (0 files). Placeholder packages with no code. Their existence as empty stubs does not violate the "no telemetry" / "no automatic updates" prohibitions -- there is nothing in them to violate anything. Left untouched; Phase 7 does not populate them (out of scope).
- **`owner_control_center/`** and root-level `tests/` and `deployment/` -- entirely empty directory trees (0 files each), never committed (no git history touches them at all), not gitignored. Dead scaffolding, unrelated to the live `owner/` app or the live `products/*/tests`. Left untouched -- not in scope to clean up, and deleting them isn't part of this phase's mandate.

## Confirmed live architecture (read before any implementation, per Part A)

- **Retail/Clinic Windows**: standalone Flask apps (`products/*/backend/app.py`), each importing shared blueprints from `commercial_runtime.identity` (auth/onboarding/multi-tenant auth/registry) and `commercial_runtime.backup`. Loopback-only CORS, per-installation random secret key, `/api/health` (frozen contract, launcher readiness probe) and `/api/version` (open contract, carries `product_code`) already exist and are the natural home for a licensing-status extension.
- **Windows launcher** (`products/*/desktop/launcher_*.py`): explicit `LauncherState` machine, binds one exclusive loopback socket before starting the server (closes a real historical TOCTOU port-race), verifies `/api/version`'s `product_code` after readiness to prevent cross-product mix-up, opens a native `pywebview` window with Edge/Chrome/default-browser fallback. This is the natural place to insert a pre-window activation-required redirect, not a new launcher subsystem.
- **Android**: Chaquopy embeds the *same* Python backend (Flask + `commercial_runtime`) directly in-process on-device (not a separate server), reached from Kotlin/Compose via Retrofit/OkHttp against `127.0.0.1`. `minSdk 26`, `targetSdk 34`, Chaquopy Python 3.12. Current Chaquopy `pip` install list is trimmed to `Flask`, `Werkzeug`, `flask-cors`, `waitress`, `openpyxl`, `requests` -- **`cryptography` is not currently installed on Android**, and adding it must be verified against Chaquopy's prebuilt-wheel support for `arm64-v8a`/`x86_64` before being relied on (tracked as a real risk in `phase7-threat-model.md`, not assumed away).
- **Android signing**: `keystore.properties`-driven `signingConfigs.release`, read from `android/aura-<product>/keystore.properties` (gitignored, never committed) -- rc.2 must reuse whatever real keystore already produced the rc.1 signed artifacts; Phase 7 must never generate a replacement production keystore.
- **Product mutation-route inventory** (used to build the Part Q/R capability matrices): enumerated by grepping every `@clinic_bp.route` / `@retail_bp.route` decorator in `products/clinic/backend/api/clinic_api.py` and `products/retail/backend/api/retail_api.py` -- see `clinic-restriction-capability-matrix.md` and `retail-restriction-capability-matrix.md` for the full mapping from route to capability code.

## Baseline verification commands run

```
git status --porcelain=v1
git log -1 --oneline
git tag --list
OWNER_TEST_DATABASE_URL=postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/aura_owner_test \
  .venv/Scripts/python.exe -m pytest owner/tests/ -q
.venv/Scripts/python.exe products/run_all_tests.py
```

Results (both runs completed): Owner `179 passed in 239.62s`; Retail+Clinic `20 file(s) run, 20 passed, 0 failed` (176 Retail tests, 116 Clinic tests, 5 `commercial_runtime` migration-safety tests). Full detail in `phase7-implementation-plan.md`'s baseline section. Android unit/lint/build baselines were not run in this pass -- deferred to Part F/AA when the Android toolchain is actually invoked, recorded as an explicit gap rather than assumed clean.
