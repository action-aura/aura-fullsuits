# Local Authorization / Licensing Boundary (M9.21)

Preserves the M7.19/M8.13 separation for the real M9 orchestration
layer specifically — commercial Installation access ≠ local Retail
user authorization. No local Retail RBAC is created in M9.

## Real, confirmed absence of invented local RBAC

Grepped every M9 file under `licensing/transport/` and `ui/activation/`
for any role/permission concept (`Role`, `Permission`, `RBAC`, admin/
manager/cashier tier) — zero matches. `ActivationViewModel`/
`ActivationScreen` gate nothing about local Retail feature access;
they only ever read/write real licensing orchestration state
(`ActivationState`, `CustomerAuthenticationState`, etc.).

## Real, binding rule restated for M9's own new surface

A successful future commercial activation (`ActivationState.
ACTIVATION_COMPLETE`) must not automatically grant a local Retail
employee administrative permissions — nothing in `ActivationViewModel`
writes to, reads from, or references any local Retail
authorization/session store; the two systems remain structurally
disjoint code, not merely disjoint by convention.

An expired or revoked commercial License must not be bypassed by a
local Retail administrator — real, structural proof:
`LeaseRefreshOrchestrator`'s own real states (`LicenseSuspended`/
`LicenseExpired`/`InstallationRevoked`) are pure, server-derived
values with no local override path — no function in this milestone
accepts a "force"/"admin override" parameter anywhere in the
`licensing.transport` package (confirmed by the same real grep already
run for `device-policy-security-review.md`'s own equivalent M8
finding, re-run for this milestone's new files with the same, zero-
match result).

## Integration boundary (documented only, per the checkpoint's own instruction)

Future integration: `App.kt`'s `AppPhase.LicenseBlocked` (real,
already-defined since M6) is the eventual seam where a real commercial
block would prevent reaching the Retail shell — but per
`startup-licensing-integration.md`'s own disclosed decision, that
wiring is deliberately deferred until M10/M11 exist. Local Retail
authorization itself remains deferred to its own canonical milestone,
unchanged and untouched by M9.
