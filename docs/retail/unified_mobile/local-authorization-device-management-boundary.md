# Local Authorization vs. Device Management Boundary (M8.13)

Extends `local-authorization-vs-license-entitlement.md` (M7.19) to the
new M8 device-management surface specifically. Preserves the same
separation; does not redefine it.

## Real composition, extended

Commercial device-management actions
(`device-replacement-transfer-contract.md`'s `Deactivate*`/
`RequestDeviceReplacement*`/`ReplaceLost*`/`ReplaceRevoked*` requests)
may require, per M7.19's own four-term `AND` composition:

```
commercial_device_management_access(installation, action) =
      valid_customer_account_or_equivalent_authority(action)   // OWNER-CUSTOMER-AUTH-PREREQUISITE-SPEC.md, item 14 — real, currently absent for account-gated actions; possession-based actions unaffected
  AND valid_license_ownership(installation, license)             // item 15 of the same spec
  AND installation_credential_present(installation)               // real Ed25519 device-key possession, already proven in M7
  AND recent_reauthentication_if_required(action)                  // mirrors require_recent_auth's real staff-side pattern
  AND server_entitlement(action)                                    // real server-side authorization decision, never assumed client-side
```

Local Retail RBAC (the existing, pre-M7 employee/PIN model) may
control **whether a local employee can open the protected settings
screen that surfaces device-management actions at all** — a real,
ordinary local-authorization gate, no different from any other
protected Retail settings screen. Local RBAC must **never** itself
authorize the underlying Owner-side License/Installation transfer —
opening the screen and successfully performing the commercial action
remain two, never-merged decisions, exactly as M7.19 established for
license entitlement generally, now stated explicitly for this new
surface.

## The concrete rule the checkpoint asked for

**A local cashier role must not be able to deactivate a commercially
licensed device merely because that device is currently licensed and
the cashier can see a settings screen.** Concretely: even if a local
`Owner`/`Manager`-tier Retail role grants access to the device-
management screen, every `DeviceManagementOutcome` (M8.6) the client
can ever display still originates from a real server decision — no
`DeviceManagementOutcome.DEACTIVATED` is ever synthesized locally from
"the local role permits it." The shared request types (M8.6) carry no
field through which a local role could bypass server authorization —
there is no boolean "force"/"local override" parameter anywhere in
`DeviceManagementContracts.kt`.

## Consistency with `device-policy-presentation-contract.md`

`DevicePolicyPresentationState.CustomerAuthPrerequisiteMissing`
(M8.12) is the real, honest state a device-management screen shows
when the *commercial* authority (item 14 of the customer-auth spec) is
unavailable — this is orthogonal to, and never substitutes for, local
RBAC gating of the screen itself. A local user with full Retail
settings access still sees `CustomerAuthPrerequisiteMissing` until the
real Owner-side prerequisite exists; a local user without settings
access never reaches the screen at all. Both gates are real and
independent, per M7.19's own non-negotiable separation.
