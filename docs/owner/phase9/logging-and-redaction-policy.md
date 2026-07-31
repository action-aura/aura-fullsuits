# Phase 9 — Logging and Redaction Policy

## Format

Every log line: `{"timestamp", "severity", "service": "aura-owner", "message", "correlation_id"}`,
plus optional `reason_code`/`operator_id`/`installation_ref`/`license_ref` when explicitly attached via
`extra={...}` at the call site — never free-form key/value pairs that could accidentally carry a
secret through an untyped field.

## What must never appear in a log line (enforced by real redaction, tested)

Full license keys (`AURA-<PRODUCT>-<N>-XXXX-XXXX-XXXX-XXXX-XXXX` shape), `password=`/`"password":`
values, `license_key=`, `pepper=`, `totp_secret=`, `recovery_code=`, base64-looking `signature=` blobs,
and any PEM private-key block. Verified by `owner/tests/test_observability_logging.py` (7 tests): each
marker category individually confirmed redacted, and a control test confirms ordinary log text (e.g.
`"GET /health/live -> 200 (1.2ms)"`) passes through completely unmodified — redaction that over-matches
ordinary text would be its own operational problem (unreadable logs), not just under-redaction.

## Explicitly out of scope for the redaction filter (already excluded structurally, not by pattern)

Patient data, prescriptions, diagnoses, Clinic notes, Retail sales/stock/receipt data, payment-card
data — Owner's own codebase has no code path that could log these in the first place (the data doesn't
exist in Owner's database — `network-and-trust-boundaries.md`), so there is nothing for the redaction
filter to catch here; the real control is architectural, not textual.

## Rotation, retention, size limits

NOT VERIFIED against a real host this session (no real filesystem/logrotate to configure). Real
deployment: Docker's own `json-file` logging driver with `max-size`/`max-file` limits (to be added to
`docker-compose.staging.yml`'s `logging:` block once a real host exists to size these against), plus
`logrotate` for any file-based log outside the container runtime's own log capture.

## Redaction tests as the actual proof, not the policy document alone

A written policy without an enforced, tested implementation is not a control — this document only
describes behavior that `owner/tests/test_observability_logging.py` already verifies against the real
formatter code, every time the suite runs.
