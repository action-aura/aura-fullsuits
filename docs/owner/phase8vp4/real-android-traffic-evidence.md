# Phase 8V-P4 — Real Android Traffic Evidence

## Capture method actually used

Not a formal proxy/instrumentation harness (per `traffic-and-log-capture-plan.md` below) -- this
session captured real traffic evidence via the two vantage points already in place: (1) the local
licensing-status API each product exposes on its own embedded backend (a direct, real reflection of
what the Kotlin licensing layer last received from Owner), queried via `curl` over `adb forward`
after each real scenario step, and (2) Logcat (see `android-logcat-privacy.md`). Both are real,
physical, on-device evidence -- not a mock.

## What was confirmed present (allowed)

Every `status` response included: `installation_id`, `assertion_expires_at`, `license_status`,
`subscription_status`, `last_successful_checkin_at`, `last_sync_result`, `last_public_reason_code`,
`current_state`, `entitlements` (structured, empty-valued fields for this synthetic plan) --
all within the allowlist this phase's own brief defines.

## What was confirmed absent (forbidden)

- Full license key: present only in the one real `/activations` request per product (protocol
  design, reconfirmed via Logcat -- zero occurrences anywhere else).
- No patient/appointment/visit/prescription/invoice/payment content in any status response or log
  line (Clinic).
- No sale/receipt/stock/supplier/customer/total/tax/discount content in any status response or log
  line (Retail).
- No private device key, no Owner private signing key, no credentials, no internal staff notes, no
  database paths -- none of these fields exist anywhere in the local licensing-status API's own
  schema (`present_status()` / `_SAFE_TOP_LEVEL_FIELDS`, unchanged source, reconfirmed by direct
  code read this session).

## Not captured this session

Raw wire-level HTTP request/response bodies for the actual Owner<->Kotlin exchange (the real
Ed25519-signed traffic) -- Kotlin holds the device key and makes this call directly; capturing it
in flight would need either a TLS-terminating local proxy or Kotlin-side request logging, neither of
which was set up this session given time constraints. The local status API and Logcat evidence
above are a real, physical, but indirect reflection of that traffic's outcome, not the raw bytes
themselves.

## Result: allowlist compliance confirmed for everything actually observable this session; the raw
wire capture itself is a real, disclosed gap.
