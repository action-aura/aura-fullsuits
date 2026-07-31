# Phase 8V-P6 — Assertion Contract Change Decision

## Decision: no contract/assertion version bump. One additive field added to the client-side parsed
evidence shape; zero changes to the wire schema or allowlist.

`subscription_status` and `commercial_grace_end`: both already signed by Owner in every assertion
(`assertions.py`, `assertion_fields.py`), both already in `assertion_verifier.py`'s
`ALLOWED_PAYLOAD_FIELDS`. `subscription_status` is already parsed into `AssertionEvidence`.
`commercial_grace_end` is added to `AssertionEvidence` (a client-internal dataclass, not the wire
format) and parsed the same way `not_before`/`expires_at` already are (`datetime.fromisoformat`,
`None` when absent from the payload -- which is the normal case for a subscription that has never been
`PAST_DUE`). This is purely additive to the *parsing* code; the wire payload shape is unchanged, so
older/newer client and server combinations remain compatible without any version negotiation:

- An old client talking to the new Owner: the extra `commercial_grace_end` field is already
  allowlisted (Part W shipped it already) and was already being silently ignored by old clients that
  don't parse it into `AssertionEvidence` -- still true, no regression.
- A new client talking to an old Owner that doesn't populate `commercial_grace_end` at all: the field
  is simply absent from the payload; the new parsing code treats `payload.get("commercial_grace_end")`
  as `None`, same as any other optional field.

`ASSERTION_VERSION = 1` in `owner/app/licensing_service/assertions.py` is unchanged. No Android
Kotlin/Windows typed-model change is required (per the existing architecture note in
`assertion_fields.py`'s own docstring: neither platform deserializes the payload into a typed model;
`commercial_runtime` reads by key already).
