# Phase 8V-P7 — Real Device Identity Inventory

## Constraint, disclosed per this session's own governing instruction

This machine is Windows 10 Pro with no Hyper-V/Windows Sandbox available (`Get-Service vmms` and
`Get-Command WindowsSandbox.exe` both return nothing) and no second physical Windows machine. **A
genuine, independently-keyed third client identity cannot be created this session.** This directly
limits Scenario 6 (which needs a real C replacing B) and Scenario 7's fresh-identity-D
activation-block sub-check to what a single real Windows machine plus the real physical Android device
can provide.

## Identities actually available and used this session

- **A — physical Android** (Infinix X6528, real hardware, real Chaquopy-embedded product,
  `installation_id` assigned by real Owner activation).
- **B — primary Retail Windows** (`dist/AuraRetail/AuraRetail.exe`, real PyInstaller-frozen product on
  this real machine, rebuilt this session to rc.4 with the corrected `commercial_runtime`, activated
  through the real production activation API, generating its own real device keypair via
  `commercial_runtime.licensing_contracts.device_identity`'s Windows DPAPI-backed provider).
- **C / D (a genuine independent third/fourth identity)**: **not available this session.** No
  fabrication was performed in its place -- no `Installation` row was inserted directly, no private key
  cloned, no fingerprint edited.

## Correction, discovered while actually attempting Scenario 6

The pessimistic conclusion above turned out to be wrong in practice. `products/retail/desktop/
launcher_retail.py` already has automatic port-fallback (`_bind_free_socket(start=5000, stop=5020)`),
so multiple real instances of the same rebuilt `AuraRetail.exe` can run simultaneously on this single
machine, each given a distinct `AURA_APP_DATA` directory -- and each independently generates its own
real device keypair and completes real Owner activation. This is not the literal "separate OS
environment" the spec's Preferred-C list describes, but it satisfies the spec's own substantive
requirement for a legitimate identity ("runs the actual final Retail product... generates its own
private/public device key... signs its own activation request... Owner verifies the signature...
creates an installation only through the real activation API... never inserts database rows directly").
Four such real, distinct identities were actually created and used this session: B, C, D, and (briefly,
for the temporary-exception activation-block sub-check) E. See `scenario6-device-replacement-final.md`
and `scenario7-plan-downgrade-overage-final.md` for the full real evidence -- both scenarios reached
**PASS** for their Owner-side mechanics using these real identities, no VM/Sandbox needed after all.

## Remaining real limitation

The physical Android device (identity A) was disconnected for an extended period during this session
(see `phase8vp7-baseline.md`'s connectivity notes) and could not be brought into the Windows-only
multi-instance sequences above. Scenario 6/7's own mechanics are platform-agnostic by construction
(confirmed by reading `replace_device_slot()`, which operates purely on `Installation` rows), so this
does not weaken the Owner-side proof, but it does mean neither scenario's own Android-specific
check-in/assertion-confirmation sub-steps were independently re-verified this session -- disclosed in
each scenario's own final document rather than silently assumed.
