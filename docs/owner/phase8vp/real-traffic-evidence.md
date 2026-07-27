# Phase 8V-P — Real Traffic Evidence (Part O)

## Tier: genuinely real this time — the actual installed products, not a test harness

Every request/response body referenced below was captured from real HTTP calls between the actual
installed `AuraClinic.exe`/`AuraRetail.exe` (rc.3) and the actual running Owner server (real
Postgres, real Ed25519 key, real trust anchor) during Scenarios 1, 2, 4, 6, and 7 — see each
scenario's own evidence doc for the literal captured JSON (Scenario 1's doc embeds a full real
activation request/response pair verbatim, key/signature values truncated only in the markdown for
readability).

## Allowlist verification, across every real call this session

**Request fields observed** (activation and check-in calls, real, both products): `contract_version`,
`request_id`, `correlation_id`, `timestamp`, `nonce`, `product_code`, `platform`, `app_version`,
`release_channel`, `installation_id`, `device_public_key`, `device_public_key_algorithm`,
`license_key` (activation only), `idempotency_key`, `signature` — every one is on the governing
brief's allowed-request-fields list. `license_key` appeared **only** on the five initial activation
calls (Scenarios 1, 2, 4, and the three device activations across Scenarios 6/7); it is structurally
absent from every renewal/check-in/revival/conversion/replacement/downgrade call afterward, because
`LicensingClient.check_in()`'s body has no key-shaped field to put one in.

**Response fields observed** (real signed assertion payloads, both products, post-Milestone-7
fields included): `assertion_id`, `product_code`, `license_public_id`, `installation_public_id`,
`license_status`, `installation_status`, `subscription_status`, `allowed_device_count`,
`device_key_fingerprint`, `entitlements`, `offline_policy`, `commercial_policy_version`,
`renewal_status`, `plan_code`, `term_start`, `term_end`, `past_due_since`, `commercial_grace_end`,
`pilot_status`, `emergency_extension_id` — every one on the allowed-response-fields list, and every
one independently re-verified against `assertion_verifier.py`'s own `ALLOWED_PAYLOAD_FIELDS`
allowlist (the exact gate that rejects anything not on this list — the fix from Phase 8V's own
session is what makes the nine Part W fields pass it at all).

## Forbidden-content sweep, real captured bodies

Every real request/response body from Scenarios 1, 2, 4, 6, and 7 was checked against the full
forbidden list (patients, appointments, prescriptions, medical notes, Clinic invoice/payment data,
Retail sales/sale-lines/receipts/stock/suppliers/customers/transaction-totals/gross-profit, local
database paths/contents, employee records, contact lists, telemetry, private device/Owner signing
keys, internal staff notes, full license key post-activation) — clean. Zero matches.

## What was not captured this session

A raw network-level packet capture (Wireshark or equivalent) was not run — the evidence above is the
actual application-level JSON bodies exchanged between two real processes over a real (loopback)
socket, which is what a packet capture would show at the HTTP layer anyway. TLS was not exercised
because Owner was validated over plain `http://127.0.0.1:5551` (a real local loopback validation
server, never a public endpoint) — `requests`' TLS verification has nothing to check against a
non-`https://` URL regardless of `OWNER_LICENSING_VERIFY_TLS`. That setting itself was never
weakened: `products/*/backend/config.py` hardcodes it `True` whenever `sys.frozen` is set (true for
both real frozen builds used this session) independent of any environment variable, exactly as
designed — no commercial artifact's TLS-bypass posture was touched.
