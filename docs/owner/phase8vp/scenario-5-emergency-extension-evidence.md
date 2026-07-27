# Phase 8V-P — Scenario 5: Emergency Extension (Clinic) — REAL EVIDENCE

**Tier**: real Owner server, real staff HTTP session, real TOTP-based MFA (`pyotp` against a real
enrolled secret, not simulated). **Android/Windows product leg**: not separately re-run this
scenario — the assertion-carries-the-extension claim is already proven by the same
`resolve_commercial_assertion_fields()` mechanism Scenarios 1/2/4 exercised live
(`emergency_extension_id` field), and by `owner/tests/test_commercial_ops_assertion_fields.py`
(still green). **Physical Android**: NOT VERIFIED.

## A second real environment gap found and fixed

The Owner UI itself returned `403` for a real, `is_super_admin=True` staff account — not a code
defect: the persistent `aura_owner_dev` database's `owner_permissions` table was seeded from an
older revision of `seed_data.py`, missing 10 permission codes added by later Phase 8 milestones
(`pilots.*`, `emergency_extensions.*`, `activation_policy.manage`, `pending_activations.*`,
`device_slot_exceptions.*`). `is_super_admin`'s wildcard bypass grants every permission *that exists
as a row* — a permission that was never inserted can never be granted to anyone, super admin
included. Fixed by inserting the missing `Permission` rows (and re-syncing named-role mappings) from
the current, canonical `seed_data.py` — a data-sync operation, not a schema or code change.

## Real MFA-gated creation, real validation

Real login -> real TOTP MFA verification (this itself starts the recent-auth window, confirmed by
the create route working immediately after without a separate `/auth/reauth` round-trip needed —
consistent with this app's session design, not a gap). Real attempts, all genuinely rejected by the
live server:

- Empty reason: `400`.
- Duration `9999` hours (cap is 72): `400`.
- A second extension on the same still-active subscription (stacking): `400`.

Real successful creation: `reason="...real customer-site outage..."`, `incident_reference="INC-8VP-5"`,
`duration_hours=6` -> `302` (created). Database confirms `starts_at`/`expires_at` exactly 6 hours
apart, `status=ACTIVE` at creation.

## Real revoke

`POST .../revoke` with a reason -> `302`. Database confirms `status=REVOKED`,
`revoked_at` populated, real timestamp.

## Principles proven live, not just by code inspection

- No indefinite extension (hard 72h cap enforced server-side, not just in the form's `max=` attribute).
- No stacking.
- Mandatory reason on both create and revoke.
- Paid term and payment records untouched — this scenario never called any renewal/payment function
  at all, structurally impossible for it to have changed either.

## Result: **PASS** (Owner-side, real HTTP/MFA). Physical Android/Windows on-device leg: **NOT
VERIFIED**.
