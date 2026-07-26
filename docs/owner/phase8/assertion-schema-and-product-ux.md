# Phase 8 — Assertion Schema Finalization, PENDING Activation UX, Security/Fraud Checklist (Parts U/W/Y, Milestone 7)

## Scope note

Milestone 7's full spec scope ("full internal Owner UI workflow set... product renewal/expiry UX
strings... assertion schema extensions finalized... Part Y's full fraud/security control checklist")
is large enough to span several sessions on its own. This milestone delivers the parts that were
both genuinely blocking (Milestone 5 explicitly deferred enabling `MANUAL_APPROVAL`/`RISK_REVIEW`
against a real client build until this milestone shipped matching Kotlin/Windows handling) and
tractable to verify for real in this pass: the assertion schema extension (Part W), the PENDING
activation decision now genuinely reaching both product UIs (the Part U slice this unblocks), and an
explicit, executable Part Y checklist. Still outstanding, listed at the bottom.

## Discovery first — the actual gap was smaller than it looked

Before writing any code, a research pass established the real shape of the problem:

- **The Android Kotlin client never deserializes the assertion payload or the activation response
  into a typed model.** `OwnerClient.kt`'s `activate()`/`checkIn()`/`deactivate()` return the raw
  JSON response string verbatim and forward it untouched to the embedded Python backend
  (`LicensingCoordinator.kt`) — parsing and re-serializing via Gson would corrupt ints to doubles
  and break signature re-verification, so this is deliberate, documented Phase 6/7 design, not an
  oversight. **Adding new assertion payload fields requires zero Kotlin changes.**
- **The Windows desktop app consumes `commercial_runtime.licensing_contracts` directly in Python**
  (no HTTP-localhost hop the way Android has) — `perform_activation()` and `ingest_activation_response()`
  are the exact same functions Android's `/_internal/sync-activation` route calls for its half. One
  fix in one place covers both platforms' actual decision logic.
- **The genuine gap was one function**: `ingest_activation_response()` treated any `result != "SUCCESS"`
  identically, including a brand-new `"PENDING"` result (Milestone 5) — a legitimately-pending
  activation was raised as `ActivationFailed` with a misleading reason. This is the only place that
  needed new branching logic.
- **No Kotlin/Python conformance fixture files exist anywhere in the repo**, despite doc references
  describing the intent (`docs/licensing/phase7/product-integration-architecture.md`). Both
  `test_canonical.py` and Kotlin's `CanonicalTest.kt` embed vectors inline instead. This is a
  pre-existing documentation/reality gap, not something this milestone's PENDING work needed to
  touch — noted here rather than silently worked around, and left for a dedicated pass if genuinely
  wanted later.

## `ActivationPending` — a distinct, non-failure outcome

`commercial_runtime/licensing_contracts/activation.py` gains `ActivationPending` (deliberately NOT a
subclass of `ActivationFailed` — a caller that only catches `ActivationFailed` must never mistake
"awaiting a human decision" for "rejected"). `ingest_activation_response()` now checks for
`result == "PENDING"` before the `!= "SUCCESS"` failure branch, raising `ActivationPending` with the
Owner-assigned `installation_id` and reason code. No local state is persisted or changed — there is no
assertion yet to verify or store. `perform_activation()` (Windows path) and Android's sync-activation
path both flow through this one function, so both platforms get the fix from a single change.
`EVENT_TYPES` gains `"ACTIVATION_PENDING"` for the local privacy-safe event log.

Both HTTP entry points in `routes.py` (`/activate` for Windows, `/_internal/sync-activation` for
Android) now catch `ActivationPending` and return **HTTP 202** (not 200, not 400) with
`{"result": "PENDING", "reason_code", "installation_id", "detail"}` — a distinct status code from
both the 200 success and 400 failure paths, so a caller inspecting status code alone (the Windows
frontend does) gets a correct signal even without reading the body.

## Product UX — PENDING now reaches both platforms' real screens

- **Windows** (`products/clinic/frontend/licensing.js`, `products/retail/frontend/licensing.js`):
  the activation click handler branched on `status === 200 && body.result === 'SUCCESS'` before,
  falling through to a REASON_MESSAGES-driven error for anything else — including what would have
  been a 202 PENDING. Now has an explicit `status === 202 && body.result === 'PENDING'` branch
  showing an informational (not error) "awaiting manual approval" message.
- **Android** (`LicensingScreen.kt`, both `aura-clinic` and `aura-retail`): the activation coroutine
  branched on `result["result"] == "SUCCESS"` only (HTTP status is ignored entirely by
  `executeLocal()`, by design, since the embedded backend's body is the actual signal). Now has an
  explicit `result["result"] == "PENDING"` branch, same message, added to both products' `tr()`
  string maps (Clinic already had full Arabic coverage for its licensing screen and gets a real
  Arabic translation; Retail's licensing screen has no Arabic entries at all yet — a pre-existing gap
  from before this milestone, not backfilled here since it's a bigger, separate scope; `tr()`'s own
  documented fallback-to-English behavior means this degrades gracefully, not silently wrong).

**Not run through a Gradle/JS build or a physical device in this pass** — these are plain,
syntactically-mirrored edits of an existing, working branch (`if result == SUCCESS ... else ...`
becoming `if SUCCESS ... else if PENDING ... else ...`), but per this project's own
evidence-over-narrative discipline (Phase 7V-A), that is stated here explicitly rather than implied.
Physical/build verification is Milestone 8's job alongside the other Part AB scenarios.

## Assertion schema extension (Part W)

Nine new fields, resolved by `commercial_ops/assertion_fields.py::resolve_commercial_assertion_fields()`
and merged into every `build_assertion_payload()` call (activation AND check-in — see that module's
own docstring for the full field list and resolution rules). All nine pass the existing
`FORBIDDEN_ASSERTION_MARKERS` guard unchanged. `docs/owner/phase6/signed-assertion-design.md` updated
with a Part W addendum documenting the full field list rather than rewriting the original Phase 6
content. As established above, this requires zero product-side parsing changes on either platform.

## Part Y — security/fraud control checklist as explicit tests

`owner/tests/test_phase8_security_fraud_controls.py`, one test per control:

1. Deny-by-default (`resolve_commercial_state()` on an unrecognized status denies everything)
2. `REVOKED` always wins, never overridden by an emergency-extension override
3. Emergency extensions require a reason and a hard time cap
4. Over-limit remediation never touches an installation itself
5. Every `*StatusHistory` relationship this codebase relies on is structurally verified to never
   cascade `delete-orphan` (inspected via SQLAlchemy's mapper introspection, not just documented)
6. Separation of duties: a renewal's creator can never also approve it
7. No plaintext license key ever persists in `ActivationRequest` or is echoed in a response
8. The shared, loosely-gated `Subscription` transition table keeps `EXPIRED` terminal (the
   Milestone 2 security-lesson regression guard)
9. Every commercial state change produces a real `AuditLog` row
10. A `PENDING_ACTIVATION` installation is never force-flipped to `ACTIVE` by a bare retry
11. The dashboard summary surface exposes only aggregate/metadata keys, never raw PII/financial
    field names

Several of these overlap with per-milestone test files by design — this file is the single,
auditable checklist Part Y asks for, not a replacement for that other coverage.

## What Milestone 7 deliberately does not do yet

- No full internal Owner UI workflow set for pilots/emergency extensions/pending
  activations/device-slot exceptions/activation-policy management/queues — still service-layer only
  (Milestones 4-6's own stated deferral). This is the largest remaining piece of the original
  Milestone 7 scope.
- No broader product renewal/expiry-warning UX (e.g. an in-app "your subscription expires in N
  days" banner) — only the activation PENDING path got product-facing strings this pass.
- No Retail Arabic licensing-screen translations (pre-existing gap, not introduced here).
- No Kotlin/Python conformance fixture files created (the doc/reality gap noted above) — left as a
  known gap rather than silently worked around.
- No physical Android/Windows build-and-run validation of the PENDING screens — Milestone 8's job.
