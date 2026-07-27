# Phase 8V — Security Review

## The one real defect found and fixed this session

**`commercial_runtime.licensing_contracts.assertion_verifier.ALLOWED_PAYLOAD_FIELDS`** (client-side,
Windows desktop and Android's embedded Python backend both use this module) is a strict allowlist --
any assertion payload field not explicitly listed causes `AssertionVerificationError` /
`ASSERTION_FORBIDDEN_FIELD`, rejecting the entire activation or check-in. Phase 8 Milestone 7 added
nine new fields to the server-side assertion payload (`commercial_ops/assertion_fields.py`) and
concluded "zero client-side changes needed," based on real research showing neither platform
*types* the payload. That conclusion was correct for typed deserialization but missed this separate,
independent, stricter allowlist gate -- **every assertion issued since Milestone 7 shipped would
have failed client-side verification**, which would have silently broken every real activation and
check-in the moment a real Windows or Android client tried to use one.

This was not caught by any of Phase 8's 357+214 pre-existing automated tests, because none of them
exercised the real cross-package wire path -- Owner's own tests mock nothing but never call
`commercial_runtime`'s verifier; `commercial_runtime`'s own tests use hand-built fixture payloads
that were written before Milestone 7 and never regenerated from a real Owner response. It was caught
within minutes of building Phase 8V's live-wire scenario harness (`test_phase8v_scenario_live_server.py`),
which is exactly the kind of defect a live-traffic validation phase exists to find.

**Fix**: added the nine field names to the allowlist (`commercial_runtime/licensing_contracts/assertion_verifier.py`).
Minimal, additive, tested: `commercial_runtime`'s own 214-test suite reconfirmed green immediately
after, and the same live-wire scenario that found the bug now passes end to end, including a fresh
signed assertion being independently verified client-side after a real renewal.

## Everything else reconfirmed, not re-derived

Every non-negotiable principle Phase 8 Milestones 1-8 established was re-verified as still holding
under the new UI surface, not re-argued from scratch: deny-by-default, separation-of-duties
(`test_full_renewal_workflow_via_ui`'s self-approval-still-rejected assertion), recent-auth/MFA on
every sensitive action, mandatory reasons on every irreversible-ish action, append-only history
(structurally verified again via `inspect(model).relationships[...].cascade` in Milestone 8's own
test, unaffected by this phase), no secret display (grep-clean across every new template), the
`EXPIRED` shared-table-stays-terminal regression guard (unchanged, still green).

## One documented, deliberately-not-fixed low-severity gap

See `owner-ui-security-and-accessibility.md`'s "a finding this pass deliberately did NOT fix" section
(the generic `installations.transition` route's optional reason vs. the new mandatory-reason
`release_device_slot()`/`replace_device_slot()` routes) -- real, low-severity (restricting-only
transitions), out of scope for a closure-and-validation phase per its own instruction not to
redesign the Phase 8 domain.
