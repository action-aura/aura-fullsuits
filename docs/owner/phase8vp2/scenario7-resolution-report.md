# Phase 8V-P2 — Scenario 7 Resolution

## The fix

`owner/app/commercial_ops/renewal_requests.py::apply_renewal_request()`: when
`renewal.device_allowance_after is not None` (already the existing gate for writing
`subscription.device_allowance`), every `License` row whose `subscription_id` matches this
subscription is now also locked (`SELECT ... FOR UPDATE`, same pattern `activation.py` already uses
when checking a license's device limit, so a concurrent activation attempt against this exact
license serializes against this write rather than racing it) and its `device_limit` set to the same
new value, but only if it actually differs (no-op writes are skipped so no audit noise is generated
for a renewal that doesn't change the allowance).

This deliberately:

- **Does not touch any `Installation` row.** An existing active device is never silently deactivated
  by a lowered allowance — that would violate this project's own "no silent deactivation" principle,
  already enforced elsewhere (`release_device_slot()`/`replace_device_slot()` require an explicit
  reason). New-activation blocking and overage remediation remain
  `scan_over_limit_licenses()`'s job (`device_slot_ops.py`), unchanged, run separately.
- **Preserves the transaction's single-commit invariant.** The function's own docstring says the
  whole apply operation commits exactly once. `audit_record()` commits on its own (by design, Part
  T), so the per-license before/after values are collected in a list during the locked update and the
  actual `LICENSE_DEVICE_LIMIT_SYNCED` audit rows are written after the function's one real commit,
  right alongside the pre-existing `RENEWAL_APPLIED` audit call.
- **Updates every License row under the subscription**, not just the first found — a subscription can
  back more than one license (e.g. issued separately per platform), and Scenario 7's own requirement
  is that the new allowance takes effect, full stop.

No change to `commercial_runtime`, no change to any product (Windows or Android) — the gap and its
fix are entirely Owner-side; a product's own enforcement of whatever `device_limit` Owner sends it
was never the broken part.

## Why this does not violate the "no new commercial-operations capability" boundary

The Phase 8V-P session correctly declined to build this because, at the time, it was unclear whether
completing the sync was in-scope for a validation-only phase. This session's own governing brief
explicitly reclassifies it: "If the gap is a real defect... implement the smallest correct fix." The
change is four lines of logic inside a function that already writes several other subscription
fields as part of applying an already-existing renewal-request workflow — it does not add a new
route, a new workflow, a new state, or a new user-facing capability. Nothing about renewal creation,
approval, or the shape of `device_allowance_after` changed.

## Audit trail

New action code `LICENSE_DEVICE_LIMIT_SYNCED` (entity_type `license`), following the existing
codebase convention (see `LICENSE_CREATED`, `DEVICE_REGISTERED`, `RENEWAL_APPLIED` — all free-text,
no fixed enum/allowlist to register it against). Records `{"device_limit": <old>}` /
`{"device_limit": <new>}` and links the reason back to the renewal request ID.
