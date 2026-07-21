# Phase 6 -- Activation Simulator Guide (Part U)

`owner/tools/activation_simulator/` -- a standalone CLI proving the real protocol end-to-end. Imports nothing from `products/*` or `commercial_runtime/*`; reuses only Owner's own `app.licensing_service.canonical` (so it signs/verifies using the exact same canonicalization the server uses, not a second, potentially-diverging reimplementation).

## Setup
```
cd aura-fullsuits
export OWNER_SIMULATOR_BASE_URL=http://127.0.0.1:5551   # default if unset
python -m owner.tools.activation_simulator init-device
```
Generates an Ed25519 key pair at `owner/tools/activation_simulator/.device/device.pem` (gitignored, `0o600`, never uploaded). `--force` regenerates (invalidates the old device identity -- the old key must then be replaced server-side via the admin UI, not silently reused).

## Commands
```
python -m owner.tools.activation_simulator activate --license-key <FULL_KEY> [--product-code AURA_CLINIC] [--platform WINDOWS] [--installation-id <local-id>]
python -m owner.tools.activation_simulator check-in
python -m owner.tools.activation_simulator verify-assertion
python -m owner.tools.activation_simulator replay-test
python -m owner.tools.activation_simulator invalid-signature-test
python -m owner.tools.activation_simulator deactivate
```

## What each command proves
- `activate`: the full 23-step activation sequence against a real running Owner instance.
- `check-in`: authenticated check-in with **no license key in the request at all** (inspect `.device/state.json` -- it never contains one).
- `verify-assertion`: independently verifies the locally-stored signed assertion against Owner's **published** public keys (`GET /signing-keys`), not by trusting the database directly -- the same verification path a real future Retail/Clinic client would use.
- `replay-test`: sends one signed check-in, then replays the byte-identical request, and reports whether the second was rejected `NONCE_REUSED` (it always is, against a correctly-running server).
- `invalid-signature-test`: sends a check-in with a deliberately garbage signature and reports whether it was rejected `INVALID_SIGNATURE`.
- `deactivate`: revokes the device key server-side; a subsequent `check-in` will then fail.

## Safety
Synthetic test licenses only -- the simulator has no special access to real customer data and never claims to. It never logs a full license key beyond the one CLI argument it was given for a single `activate` call (never written to `state.json`, never printed a second time). Generated device private keys live only under the gitignored `.device/` directory.

## Running it against the real dev server
```
flask --app owner/app:create_app run --port 5551   # with OWNER_EXTERNAL_API_ENABLED=true
flask licensing generate-signing-key && flask licensing activate-signing-key <key_id>
# create a synthetic customer/subscription/license via the internal Owner UI or a script (see activation-e2e-evidence.md)
python -m owner.tools.activation_simulator init-device
python -m owner.tools.activation_simulator activate --license-key <the-one-time-revealed-key>
```
