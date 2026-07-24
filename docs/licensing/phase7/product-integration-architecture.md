# Phase 7 -- Product Integration Architecture

## Where the shared licensing domain lives

Two implementations of one state machine, not one shared codebase -- Python and Kotlin cannot share a runtime, so Part C's ten concepts are built twice with identical semantics, cross-checked by shared JSON conformance fixtures (a set of canned Owner responses -- success, rejection, malformed, wrong-product, expired -- that both test suites replay and must reach the same classified outcome for).

**Python side** (`commercial_runtime/licensing_contracts/`) -- imported by both Retail and Clinic Windows backends directly, and by both Android apps' embedded Chaquopy backend (since `commercial_runtime` is already staged into the APK by `stageAuraPython`). One implementation, two consumers.

**Kotlin side** (new module under each `android/aura-<product>/app/src/main/java/com/actionaura/<product>/licensing/`) -- cannot be shared as a single Gradle module across the two independent Android projects without a bigger restructuring than this phase's boundaries allow (each app is its own Gradle project, no existing shared Kotlin module exists to extend). Instead: one reference implementation written once, the second product's package created by deliberate parallel construction against the same design doc and the same conformance fixtures -- consistent with how Chart Q (Clinic first, Retail mirrors) is already sequenced.

## The ten Part C concepts, mapped

| Concept | Python location | Kotlin location | Responsibility |
|---|---|---|---|
| `LicensingClient` | `licensing_contracts/client.py` | `licensing/OwnerClient.kt` | HTTP transport to Owner only; zero authority to change local state |
| `DeviceIdentityProvider` | `licensing_contracts/device_identity.py` | `licensing/DeviceIdentity.kt` | Owns the device Ed25519 keypair; signs canonical bytes; never exposes the private key |
| `OwnerTrustStore` | `licensing_contracts/trust_store.py` | `licensing/TrustStore.kt` | Bundled + rotated trusted Owner public keys (Part D) |
| `AssertionVerifier` | `licensing_contracts/assertion_verifier.py` | `licensing/AssertionVerifier.kt` | Re-verifies every assertion independently of transport |
| `LicenseStateRepository` | `licensing_contracts/state_repository.py` (SQLite table in each product's own DB) | Kotlin does **not** get its own copy of state -- see Authority Boundary below | Durable local state (Part J's field list) |
| `LicensePolicyEvaluator` | `licensing_contracts/policy_evaluator.py` | not duplicated (evaluation happens once, in Python -- see below) | Deterministic current-state computation from signed evidence + trusted time |
| `LicenseCapabilityGuard` | `licensing_contracts/capability_guard.py` | not duplicated | Route/service-level allow/deny |
| `LicenseCheckInScheduler` | `licensing_contracts/checkin_scheduler.py` (APScheduler-style background thread, Windows) | `licensing/CheckInWorker.kt` (Android `WorkManager`, respects Doze/battery) | Triggers check-in on the platform-appropriate cadence |
| `LicensingStatusPresenter` | Flask routes returning status JSON to the existing frontend | Compose screens reading that same JSON via Retrofit | Display only, never authoritative |
| `LicensingEventRecorder` | `licensing_contracts/events.py` | writes through the embedded backend's local API, not a separate Kotlin-side log | One event log per installation, not two |

## Why Android does not get a second, Kotlin-side policy evaluator or state repository

Part U is explicit: the embedded Flask backend "must not require access to an exportable Android private key" but must **independently verify** Owner-signed assertion authenticity and **independently enforce** capabilities -- it must never trust a Kotlin boolean. Read literally, this could suggest two independent evaluators. It does not require two independent *sources of state*: Android's own product data (patients, sales) already lives in the SQLite database the embedded Flask backend owns, and every mutation route Part Q/R must guard already lives in that same Python backend (`clinic_api.py`, `retail_api.py`, staged unmodified into the APK). Splitting policy evaluation into a second Kotlin copy would create exactly the two-sources-of-truth problem Part U's "never trust a Kotlin boolean" line warns against, not solve it.

The resolved boundary: **Kotlin owns everything that requires Android-only capability** (Keystore-wrapped key material, the Owner network call, the activation UI, the background check-in trigger). **Python owns all authority** (state repository, policy evaluation, capability enforcement) -- identical to how Windows does it, because it is the same Python code (`commercial_runtime`) running in-process either way. Kotlin's role after receiving an Owner response is narrow: hand the *raw, still-unverified* response to Python's own `AssertionVerifier` over the localhost interface (Part U's "narrow localhost interface") and let Python re-derive trust from scratch -- Kotlin's own verification (which it also performs, since it needs to know *before* calling Python whether to retry or show an error) is advisory to the UI only, never a substitute for Python's independent check. This is the concrete mechanism behind Part U's "never trusting a Kotlin boolean."

## Localhost sync interface (Part U)

New endpoint, Chaquopy-embedded backend only, additive to `app.py`:

`POST /api/licensing/_internal/sync-assertion` -- binds to `127.0.0.1` only (already guaranteed, this backend never binds elsewhere), requires a per-process shared secret generated at Kotlin-process startup and passed to Chaquopy via `os.environ` before Flask boots (mirrors how `SECRET_KEY`/`AURA_APP_DATA` are already threaded through), accepts the raw Owner activation/check-in response body plus the Kotlin-side device-signature proof that this device produced the matching request, and does nothing but hand the payload to the same `AssertionVerifier`/`LicenseStateRepository` code path Windows uses directly. A raw "unlock" command has no representation in this endpoint's request schema -- it accepts an Owner response envelope or nothing at all.

## Authority boundary summary (Parts U/V)

| Layer | Can decide licensing state? | Can call Owner? | Can hold device private key? |
|---|---|---|---|
| Windows UI / frontend JS | No | No | No |
| Windows Python backend (`commercial_runtime.licensing_contracts` + product routes) | **Yes -- sole authority** | Yes | Yes (DPAPI-wrapped, Part E) |
| Android Kotlin/Compose | No (advisory display only) | Yes (only layer with Keystore access) | Yes (Keystore-wrapped, Part F) -- but never exports it, including to Python |
| Android embedded Python backend | **Yes -- sole authority**, independent of Kotlin's own check | No (Kotlin makes the actual HTTPS call; Python only receives the resulting envelope over localhost) | No |

This table is the concrete answer Part A requires before any implementation begins.
