# Phase 8V-P7 — Raw Wire Evidence (redacted excerpts)

Same real WSGI middleware as prior sessions, same redaction rules. 10 real exchanges this session: 1
`service-info`, 3 `check-ins` (physical Android), 6 `activations` (real Windows product instances --
one real mis-peppered-key failure I found and fixed myself, mid-session, plus the real Scenario 6/7
multi-instance sequence).

## Physical Android check-ins (post-rc.4-upgrade)

1. **Retail baseline** (before Scenario 2's restriction test): `subscription_status: "ACTIVE"`,
   device state `Active`. Confirms the rebuilt/upgraded Retail app behaves identically to before for
   the normal case.
2. **Clinic Scenario 3 smoke check** (`installation_id c7150980-...`, still `subscription_status:
   "EXPIRED"` from Phase 8V-P6): `Restricted` reconfirmed on the rebuilt rc.4 artifact -- the fix
   survives the rebuild cycle.
3. **Retail Scenario 2 restriction** (assertion for installation `e77bd448-...`):
   `subscription_status: "EXPIRED"`, `license_status: "ACTIVE"`,
   `offline_policy.hard_expiry_behavior: "WARN_ONLY"` -- the same unconfounded proof pattern as Clinic's
   Scenario 3, now demonstrated on Retail specifically.

## Windows product activations (real, redacted)

- One real failed activation with `reason_code: ACTIVATION_REJECTED` (the mis-peppered first key
  attempt, self-caught by inspecting this exact captured evidence -- `license_key` and `signature` were
  both already redacted even in the failed attempt).
- Real successful activations for identities B and C (`platform: "WINDOWS"`, real `device_public_key`
  per instance, real signatures, `license_key` redacted).
- Real `DEVICE_LIMIT_REACHED` rejections for identities D (first attempt) and E.
- Real successful activation for identity D (retry, after B's replacement freed a slot).

No Clinic domain data (patient/invoice/appointment), no Retail domain data (product/sale/stock), and no
full license key appear in any of the 10 exchanges. `signature` is redacted in every entry.

## Scope disclosure

Late renewal, past-due-specific, plan-downgrade check-in, temporary-exception check-in, and
stale-assertion traffic were not generated **from the physical Android device** this session (the
Owner-side halves of renewal/downgrade/exception were real and executed, but the confirming device
check-ins were blocked by the extended physical disconnection -- see the individual scenario docs).
