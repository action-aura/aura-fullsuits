# Phase 8V-P5 — Raw Wire Evidence (redacted excerpts)

Captured by the real WSGI middleware described in `raw-wire-capture-plan.md`, wrapping the actual
Owner Flask app's own `wsgi_app`, scoped to `/api/licensing/v1/*`, redacting `license_key`/`signature`/
`password`/`totp_secret`/`recovery_code` in-memory before anything reached disk. Source temp file
(`capture_raw.jsonl`, 7 real entries) deleted at session cleanup per Part V after this excerpt was made.

## Honest scope disclosure

Only **7 real exchanges** were captured this session: 2 `service-info`, 1 `activations`, 4 `check-ins`
(one rejected/SUSPENDED, three accepted -- including the post-timezone-fix emergency-extension one). The
governing spec's Part D asks for capture across standard check-in, late renewal, past-due, emergency
extension, device replacement, plan downgrade/overage, and temporary device exception refreshes. Only
**standard check-in** and **emergency-extension check-in** were actually captured; late renewal,
past-due, device replacement, plan downgrade/overage, and temporary-exception exchanges were not
reached this session (see the corresponding scenario docs, all reported NOT VERIFIED). This doc reports
exactly what exists, not what was planned.

## Entry: real device activation (Clinic, rc.3)

```json
{
  "timestamp": "2026-07-31T03:36:40.933871+00:00",
  "method": "POST", "path": "/api/licensing/v1/activations", "status": "200 OK",
  "request": {
    "contract_version": "v1", "request_id": "9cae1e37-d4ea-455e-8944-2eaa873348bc",
    "correlation_id": "01cd76da-1e3b-4822-b65b-d52befcf5e24",
    "timestamp": "2026-07-31T03:36:40.085956Z", "nonce": "By2-AmPTIJac8g47NVp3vwc5moA2HwXd",
    "product_code": "AURA_CLINIC", "platform": "ANDROID", "app_version": "1.0.0-rc.3",
    "release_channel": "rc", "installation_id": "5d7df98d-c609-49aa-8bb1-b4c0ef2d02cf",
    "device_public_key": "sDfpwXE6hOS3KhF1teCn1x1PZuivTgeXZ8diRaoICYA=",
    "device_public_key_algorithm": "ed25519",
    "license_key": "<redacted>", "idempotency_key": "cf440184-8de8-4358-9f05-08ee2f36beb5",
    "signature": "<redacted>"
  },
  "response": {
    "result": "SUCCESS", "reason_code": "ACTIVATION_APPROVED",
    "installation_id": "c7150980-d45b-4d14-866b-642fb798dceb",
    "signed_assertion": { "algorithm": "ed25519", "payload": { "...": "commercial fields only, see below" } }
  }
}
```
Only the activation request carries `license_key`, and it is redacted -- confirming full-key
retransmission never occurs on any subsequent request (all 4 check-in entries below carry only
`installation_id` + `signature`, no key field at all, not even redacted, because it's absent from the
payload entirely).

## Entry: rejected check-in (real SUSPENDED license)

```json
{
  "method": "POST", "path": "/api/licensing/v1/check-ins", "status": "400 BAD REQUEST",
  "request": { "installation_id": "e77bd448-...", "signature": "<redacted>", "...": "..." },
  "response": {
    "result": "FAILURE", "decision": "REJECTED", "reason_code": "ACTIVATION_REJECTED",
    "retry_guidance": "do_not_retry_without_correction"
  }
}
```
`LICENSE_SUSPENDED` is never exposed to the client -- the public reason code is the generic
`ACTIVATION_REJECTED`, confirming `reason_codes.py`'s internal-only normalization is real and working
over actual physical traffic, not just unit tests.

## Entry: successful check-in after the emergency-extension timezone fix

```json
{
  "method": "POST", "path": "/api/licensing/v1/check-ins", "status": "200 OK",
  "response": {
    "result": "SUCCESS", "reason_code": "CHECK_IN_ACCEPTED",
    "signed_assertion": { "payload": {
      "assertion_id": "34084c46-616e-4290-a6aa-59ae42f18a32",
      "device_key_fingerprint": "86c35bed5abd70ba39498eb5ceee7876f25780eea1931896d59245d0ab4181a9",
      "emergency_extension_id": "19c6048e-d35d-4698-a9a1-ccec79b10ff2",
      "commercial_grace_end": null, "commercial_policy_version": 1,
      "allowed_device_count": 1
    } }
  }
}
```
Confirms the timezone fix end-to-end: the extension created via the corrected `create_emergency_extension()`
appears correctly as `emergency_extension_id` in a real signed payload delivered to the physical device.
No patient, sale, product, customer, invoice, or payment field appears in any of the 7 captured
exchanges -- every field present belongs to the licensing contract vocabulary (contract/request/
correlation IDs, timestamps, nonces, installation/device identity, signed assertion commercial fields).

See `final-android-data-boundary.md` for the full allowed/forbidden field audit against these entries.
