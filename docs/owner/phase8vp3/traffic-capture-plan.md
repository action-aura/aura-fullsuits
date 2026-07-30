# Phase 8V-P3 — Traffic Capture Plan (for the next device session)

Not executed this session (no device). Documented now so the next session can move directly to
capture without re-deriving the approach.

## Mechanism

Preferred: temporary structured request/response logging inside `commercial_runtime`'s own client
(`licensing_contracts/client.py`) or Owner's own route handlers, gated behind an env var
(`AURA_VALIDATION_TRAFFIC_LOG=1` or similar), writing redacted JSON lines to a local file --
no external proxy, no TLS weakening, no MITM tooling needed since this is a same-machine/adb-reverse
loopback path already under our control on both ends. Alternative if that proves inconvenient in the
moment: a local `mitmproxy`/`Charles`-style loopback proxy pointed at by `adb reverse`, with its own
CA trusted only on the test device profile -- never bundled into or trusted by the release artifact
itself.

## What to capture, per scenario

Every real HTTP request/response between the Android device and the real Owner dev server, for:
activation (once, at first install only), check-in, and each of the seven commercial-lifecycle
scenarios' own triggering check-in call.

## Redaction rules (apply before saving any evidence file)

- The full activation license key: redact everywhere except the one, single, real initial-activation
  request it necessarily appears in (per this project's own "no key re-entry" principle -- it must
  never appear in check-in, renewal, pilot conversion, emergency extension, replacement, downgrade,
  suspension, reactivation, or deactivation traffic; its *presence* in one of those would itself be a
  finding, not something to redact away and hide).
- Request/response signatures: keep the first/last 8 characters only, redact the middle, unless the
  full value is specifically what's being verified in that step.
- Any bearer/session token: fully redacted.
- Any header carrying `X-Aura-Internal-Secret` (the Android-local internal sync secret) or a device
  private key: never captured in the first place -- these never leave the device process boundary in
  the real protocol, so their absence is expected and correct, not something to redact after the fact.

## Allowed vs. forbidden field checklist

Reuse the exact allowlist/forbidden-list already defined in this phase's own governing brief (Part Q)
-- request-side: contract version, request ID, correlation ID, timestamp, nonce, product code,
platform, app version, release channel, installation ID, device public key/fingerprint, assertion
reference, request signature. Response-side: commercial state, policy version, term dates, plan,
renewal state, commercial grace, pilot/emergency-extension metadata, device allowance, active device
count, assertion state version, entitlements, safe reason codes. No patient, appointment, visit,
prescription, invoice, or payment data (Clinic); no sale, receipt, stock, supplier, customer,
transaction-total, tax, or discount data (Retail); no database paths, private keys, credentials,
staff notes, contacts, or general telemetry (either product).

## Where evidence goes

`docs/owner/phase8vp3/real-android-traffic-evidence.md` and
`docs/owner/phase8vp3/android-data-boundary.md` (per-scenario captured JSON, redacted per above, plus
an explicit pass/fail line for the allowlist/forbidden-list review) -- not created this session, since
there is nothing real to put in them yet.
