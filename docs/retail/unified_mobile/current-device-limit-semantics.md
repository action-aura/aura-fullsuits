# Current Device-Limit Semantics (M8.2)

Real, current, executable Owner semantics — restated from M7's own
findings for M8's direct use, with no reinterpretation.

## Real facts (unchanged since M7, cited not re-derived)

- `License.device_limit` is the current, sole, enforced total-active-
  Installation cap (`subscription-entitlement-license-map.md`,
  `owner-licensing-authority-audit.md`).
- `ProductPlatform` determines platform eligibility at the catalog
  level; `License.allowed_platforms` determines it at the per-license
  level (`platform-authority-audit.md`).
- No proven per-platform sub-cap authority currently exists —
  `DEVICE_POLICY_PLATFORM_CATEGORIES` (including a `MOBILE` combined
  category) is real schema, not found wired into
  `process_activation()`'s real enforcement path
  (`multi-device-license-policy-audit.md`, M7.7, finding carried
  forward unchanged — not re-investigated in M8, since M8 modifies no
  Owner code and this finding required no further evidence).
- No client-side count is authoritative — the effective limit and
  current count are both server-resolved
  (`resolve_effective_device_limit()`,
  `count_slot_consuming_installations()`).
- Final-slot concurrency is server-owned, enforced via a real row lock
  (`activation-idempotency-contract.md`).
- A same-Installation retry (matching device-key fingerprint, fresh
  client-generated `installation_id`) does not consume another slot
  (`activation-idempotency-contract.md`).

## Explicitly not invented

- No per-platform limit is modeled as active policy — only the real,
  proven total-active cap.
- `Plan.included_device_count`/`Plan.max_device_count`,
  `Subscription.device_allowance`, and the generic `max_devices`
  `PlanEntitlement` row are **not** interpreted as active policy by
  any client code — they remain the four unreconciled Owner-side
  representations documented in
  `licensing-gap-ownership-matrix.md` gap #1, entirely out of the
  client's view.

## Forward-compatible mode model

`DeviceLimitMode` enum (`shared/.../licensing/DevicePolicyContracts.kt`):

```
TOTAL_ACTIVE_INSTALLATIONS   // the only real, currently-supported mode
```

Future modes (e.g. a genuine per-platform cap, if Owner ever
implements one) must arrive as a **new, higher `policyVersion`**
(`resolved-device-policy-contract.md`) before the client enables any
different interpretation. `ResolvedDevicePolicy.validate()` rejects
any `policyVersion` this client doesn't recognize
(`MALFORMED_DEVICE_POLICY`) rather than guessing at the new mode's
meaning — a real, deliberate fail-safe, not a placeholder for future
convenience.
