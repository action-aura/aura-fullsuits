# Phase 8V — Real Traffic Evidence (Part Y)

## How this was captured

A live network packet capture wasn't possible without a running deployment and physical/emulated
client traffic (see `phase8v-scope-and-baseline.md`). The structural equivalent: a real
`werkzeug`-served Owner Flask app on a real localhost TCP port, a real Ed25519-signed activation
request built and sent via genuine `requests.post()` (not the Flask test client), and the real,
unmodified response captured verbatim. `license_key`, the device public key, and signature values
are truncated/redacted **only in this document** for readability -- the actual test assertions
(`test_phase8v_scenario_live_server.py`) check the real, full values programmatically.

## Real captured activation request (Clinic, Windows contract)

```json
{
  "contract_version": "v1",
  "request_id": "844535d5-2625-455d-b9df-ffea5fc965a9",
  "correlation_id": "e58fcbf4-0ba0-4bf2-90ca-82e22aa76dcb",
  "timestamp": "2026-07-27T03:19:31.965080+00:00",
  "nonce": "rpPEdvUqDImgvoF4aQNcnA",
  "product_code": "AURA_CLINIC",
  "platform": "WINDOWS",
  "app_version": "1.0.0-evidence",
  "release_channel": null,
  "installation_id": "3c5524db-215f-4f95-b6ca-d3ab81eabf16",
  "device_public_key": "psmdLjFeGvdw...",
  "device_public_key_algorithm": "ed25519",
  "license_key": "<REDACTED-FOR-DOC>",
  "idempotency_key": "3a694853-1f95-4b82-8211-956458e3a95e",
  "signature": "zFrg/uX6jL4x..."
}
```

Every field is on the governing brief's allowed-request-fields list, plus `license_key` -- explicitly
permitted on **initial activation only** by Part Y, and structurally absent from every subsequent
check-in/renewal-refresh call (`LicensingClient.check_in()`'s body literally has no key field --
verified by reading `commercial_runtime/licensing_contracts/client.py`, not assumed).

## Real captured activation response (signature/key truncated for this doc)

```json
{
  "result": "SUCCESS",
  "reason_code": "ACTIVATION_APPROVED",
  "decision": "APPROVED",
  "installation_id": "57c19207-118e-4fb2-86d0-94bc34343daf",
  "signed_assertion": {
    "algorithm": "ed25519",
    "signing_key_id": "owner-ed25519-20260727T031930Z-dea7483d",
    "payload": {
      "assertion_id": "d23f7cd4-174e-46a2-883e-ea98c8855866",
      "product_code": "AURA_CLINIC",
      "license_public_id": "9e5e7d9c-5027-43c5-9698-5bfe74c5d787",
      "installation_public_id": "57c19207-118e-4fb2-86d0-94bc34343daf",
      "license_status": "ISSUED",
      "subscription_status": "ACTIVE",
      "term_start": null,
      "term_end": "2026-08-01",
      "plan_code": "EVIDENCE-PLAN",
      "renewal_status": "NONE",
      "past_due_since": null,
      "commercial_grace_end": null,
      "commercial_policy_version": null,
      "pilot_status": null,
      "emergency_extension_id": null,
      "device_key_fingerprint": "c42abf8a2839e4969b72a13f616f9240f3b59bc66676f2a35ecf4e6584c856ba",
      "entitlements": { "max_devices": 0, "backup_enabled": false, "...": "product/plan-defined only" },
      "offline_policy": { "check_in_interval_seconds": 86400, "offline_grace_seconds": 1209600, "...": "technical policy only" }
    }
  }
}
```

Every payload field is on the allowed-response-fields list (the nine Part W fields included --
`term_start`/`renewal_status`/`plan_code`/etc.) -- verified both by this real capture and by
`assertion_verifier.py`'s own `ALLOWED_PAYLOAD_FIELDS` allowlist rejecting anything else (the
allowlist that had a real gap, found and fixed this session -- see `phase8v-security-review.md`).

## Forbidden-content check

Ran the full forbidden-marker list (patients, appointments, prescriptions, medical/clinical notes,
sale, inventory, suppliers, Retail customers, transaction totals, gross profit, local database paths,
employee records, contact lists, full license key post-activation) against every captured
request/response body above and against `assertions.py::FORBIDDEN_ASSERTION_MARKERS`'s own
independent enforcement (which raises before a forbidden-shaped field can ever be signed and sent) --
clean, both by direct inspection of this real capture and by the structural guarantee that produced
it. See `phase8v-data-boundary-report.md` for the broader, non-wire-traffic surfaces (queues,
notifications, reconciliation, dashboard) checked the same way.
