# Phase 7 -- Offline Enforcement Policy (local evaluation of Owner's signed policy)

Owner *defines* offline policy (Phase 6, unchanged). Phase 7 is the first place it is ever *evaluated against real elapsed time to change local product behavior* -- this document is the bridge, implemented in `commercial_runtime/licensing_contracts/policy_evaluator.py`.

## Inputs (all either signed by Owner or locally-trusted-time-derived -- never raw local wall clock alone)

- The current `signed_assertion.payload.offline_policy` object (embedded verbatim in every assertion, per `signed-assertion-design.md` -- Phase 6 already puts the full policy object, not just a policy ID, into the payload allowlist, confirmed by re-reading `external-api-data-allowlist.md` during Part A; Phase 7 does not need a new Owner call just to fetch policy).
- `assertion.payload.issued_at` / `not_before` / `expires_at`.
- Trusted-time state from `phase7-threat-model.md`'s Part N handling: last-verified-server-time, monotonic elapsed-since-last-check, local wall clock (advisory only).

## Evaluation, exactly Part O's eight rules, computed on every app-foreground/launcher-start and on a periodic local tick (not only on network events, since grace/warning must advance while genuinely offline)

1. Valid assertion (`not_before <= trusted_now <= expires_at`) + last check-in within `check_in_interval_seconds` -> `ACTIVE_ONLINE`.
2. Valid assertion, but the most recent check-in attempt failed for a connectivity reason -> `ACTIVE_OFFLINE`.
3. Elapsed offline time >= (`offline_grace_seconds` - `warning_start_seconds`) and < `offline_grace_seconds` -> `WARNING`.
4. Elapsed offline time >= `offline_grace_seconds` but a still-valid-by-date assertion exists and `hard_expiry_behavior` has not yet been applied -> `GRACE_PERIOD` (Part O's wording distinguishes "within signed grace" as its own state from "WARNING"; resolved here as: `WARNING` is the tail portion of the *check-in* cadence tolerance, `GRACE_PERIOD` begins once the check-in-interval tolerance itself is exhausted and the *offline_grace_seconds* countdown proper is running -- both draw from the same policy object, `warning_start_seconds` counting down within `offline_grace_seconds`, not two independent clocks).
5. Elapsed offline time > `offline_grace_seconds` -> `RESTRICTED`, with the specific behavior gated by `hard_expiry_behavior`: `WARN_ONLY` keeps every capability enabled but the presenter surfaces a persistent non-dismissible-until-checked-in warning; `RESTRICT_COMMERCIAL_FEATURES_PRESERVE_DATA` engages the Part P/Q/R capability guard's restricted-mode deny list. Both values are handled -- neither is treated as a no-op.
6. `installation_status=SUSPENDED` on the last verified assertion -> `SUSPENDED`, independent of any elapsed-time computation (explicit signed decision overrides timers).
7. `installation_status=REVOKED` -> `REVOKED`, same override precedence as (6).
8. `license_status` indicating expiry per the governing policy (Owner's own `valid_until` plus whatever the current `license_status` enum value signals) -> `EXPIRED`, evaluated from the assertion's own fields, not re-derived locally from a separate date comparison that could drift from Owner's authoritative decision.

## Hard limits this evaluator enforces regardless of what's stored locally

- No locally-editable configuration file, environment variable, or database row can move any of `check_in_interval_seconds` / `offline_grace_seconds` / `warning_start_seconds` upward (Part O: "Do not allow local configuration to increase Owner-signed grace"). The evaluator reads these fields *only* from the currently-verified signed assertion's embedded `offline_policy` object -- there is no code path that reads a policy value from anywhere else, so there is nothing to override.
- A local build MAY apply a *stricter* ceiling (e.g. cap grace at 7 days even if Owner's policy says 14) via a separate, hardcoded-per-build safety constant that is `min()`'d against the signed value, never `max()`'d -- consistent with "a local release may apply a stricter safety limit, but not silently extend."
- `emergency_extension_allowed` / `emergency_extension_until`, when present and in the future, extend the grace deadline used in rules 3-5 above -- this is itself a signed Owner field, not a local override, so it's read the same way as every other policy field, not treated as a special case requiring different trust handling.

## Clock-rollback interaction

Every elapsed-time computation in rules 1-5 above uses the trusted-time model from `phase7-threat-model.md` (last-verified-server-time + monotonic delta), never `datetime.now()` directly. A detected suspicious rollback (per that document) short-circuits this evaluator entirely and returns `CLOCK_REVIEW_REQUIRED` instead of computing any of rules 1-8, until online re-verification clears it.
