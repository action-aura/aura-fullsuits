# Phase 8V — Validation Environment (Part P)

## What was actually used

- Real PostgreSQL (`aura_owner_test` for the automated suite, `aura_owner_dev` for interactive/CLI
  checks, a throwaway `aura_owner_m8verify`-style scratch DB for migration reconfirmation -- dropped
  after use).
- Real Owner Flask app, `EXTERNAL_API_ENABLED = True` (always on in `TestingConfig`, per Phase 6).
- Real Ed25519 signing keys, generated and activated per-test via the existing `signing_key` fixture
  (`owner/tests/conftest.py`) under an isolated `OWNER_TEST_SIGNING_KEY_DIRECTORY`, never the dev/prod
  key directory.
- `DEBUG = False`-equivalent posture for the parts that matter (no debug reloader in the live-server
  harness; Flask's `TESTING` config flag is about exception propagation for pytest, not a wire-level
  debug/insecure mode).
- `verify_tls=False` on the `commercial_runtime` test client only, because the live-server harness
  talks to `http://127.0.0.1:<ephemeral-port>` -- there is no TLS to verify against a self-signed
  localhost cert for a throwaway per-test server, and this setting exists only inside
  `test_phase8v_scenario_live_server.py`, never in any shipped product configuration.
- Real werkzeug dev server (`werkzeug.serving.make_server`) on a real, OS-assigned, localhost-only
  TCP port -- not the Flask test client's in-process request dispatch. This is genuine HTTP over a
  real socket, request/response objects included.
- Real `commercial_runtime.licensing_contracts` client code -- the identical Python package the
  Windows desktop product ships.
- Synthetic data only throughout: no real customer, patient, business, payment, or license-key data
  anywhere in this repository or its test databases.

## What was NOT available (disclosed, not silently skipped)

- No `adb`, no connected physical Android device, no running emulator.
- No real Windows installer build-and-run cycle (the Windows desktop product's actual `.exe`/
  installer was not rebuilt and executed this session -- what WAS exercised is the identical Python
  package (`commercial_runtime.licensing_contracts`) that installer embeds, via the live-server
  harness, which is the part that actually changed/mattered for Phase 8V).

## Synthetic records created during validation

Every scenario/UI test creates its own throwaway customer/subscription/license/installation rows
(see `_make_subscription`/`_make_license_and_key`/`_make_pilot_subscription` helpers across
`owner/tests/test_phase8v_*.py`) -- Clinic and Retail products both represented, active/expired/
past-due/pilot subscriptions, confirmed and in-flight renewal requests, a manual-activation-gated
license, and a device-limited license used for the replacement scenario. Nothing here reuses a row
from any other test file's data (isolated by the `app` fixture's per-test `TRUNCATE`).
