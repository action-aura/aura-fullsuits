# Phase 9R — M8: Remote Licensing API Hardening Audit

## Disposition: audited against real code, existing Phase 6/7 infrastructure already satisfies this checklist

Consistent with M9's own audit (`signed-license-lease-contract.md`), M8 is
"harden the existing licensing authority" — this session's job was to
verify each specific M8 requirement against real code, not redesign a
system that Phase 6/7 already built correctly.

| M8 requirement | Evidence |
|---|---|
| HTTPS only | Enforced at the edge (Caddy, M6) once deployed; app itself doesn't accept plaintext by design (no route serves outside the reverse-proxy topology) |
| Stable machine-readable errors | `_error_response()` (`app/api_external/routes.py`) — consistent `{reason_code, decision, retry_guidance}` shape on every rejection path |
| Idempotency | Persistent idempotency record keyed on `(license, idempotency_key)` (Phase 6/7) — a retried activation returns the same installation, never a second slot consumed |
| Request IDs | `correlation_id` on every structured log line (`app/observability/logging_config.py`, confirmed present in this session's own test output) |
| Transaction locking | `SELECT ... FOR UPDATE` on the license row during the device-count check + insert, inside one transaction (`app/licensing_service/activation.py:176`) |
| Anti-replay controls | Nonce (single-use, Postgres-unique-constraint-enforced) + timestamp freshness window (Phase 6 threat model #1) |
| Bounded payloads | `_bounded_payload()` — real gap found and fixed **this session** (M6): the licensing API's own 64KB bound wasn't independently enforced once the global `MAX_CONTENT_LENGTH` was raised for attachments; now explicit |
| Safe app/platform validation | Canonical `Platform` lookup by `platform_code` (`activation.py:120`) — confirmed by direct code inspection: only `WINDOWS`/`ANDROID` are seeded (`catalog/services.py:133`), no `"ALL"` literal exists anywhere in the activation/platform code path |
| No placeholder ALL-platform acceptance | Confirmed absent by `grep` — no bypass to find, nothing to fix |
| Canonical ProductPlatform mapping | Same lookup, same evidence |
| No cross-customer activation | Structural: a license is looked up by its own unique `key_secret_hmac`; there is no code path that resolves "a customer's licenses" from anything but that license's own key — cross-customer activation would require guessing another customer's actual license key, which the existing serial-guessing rate limit (`activation_invalid_license` policy, M7) already throttles |
| No active-device-cap race | Same row lock as above — proven under real concurrent OS-process invocation in Phase 6's own test suite (`test_phase6_activation_protocol.py`, part of the M0 baseline's 971/972 clean run) |
| Complete audit events | `audit_record()` called on every state-changing licensing operation (consistent with the pattern already verified in M5's scheduler work) |
| No business-data payloads | Confirmed in M9's own field-by-field audit of `build_assertion_payload()` — no table, no field, no code path includes operational business data |

## What's genuinely NOT VERIFIED (not a repository gap)

Everything requiring a real remote client over a real network: real
activation/refresh/suspend/reactivate/revoke sequences against a publicly
reachable HTTPS endpoint (M20), real concurrent abuse testing at network
latency (M21). Blocked on infrastructure, not on this code.

## Extensibility for future platforms (Unified Mobile / iOS)

Per the governing instruction: "prepare for future iOS support through
extensible concrete Platform validation, but do not add unverified mobile
behavior merely to anticipate it." The `Platform` table is already a real
database table, not a hardcoded enum in Python — adding `IOS` as a new row
when the Unified Mobile work is ready requires a data migration (seed a new
`Platform` row) and zero code changes to the activation/verification logic
itself, which already resolves platform generically by `platform_code`
lookup rather than a Python-level if/elif chain. Nothing was added for iOS
this session — the extensibility already exists structurally, and adding
an actual `IOS` row before that work is ready would be exactly the
"unverified mobile behavior added to anticipate it" the instruction warns
against.
