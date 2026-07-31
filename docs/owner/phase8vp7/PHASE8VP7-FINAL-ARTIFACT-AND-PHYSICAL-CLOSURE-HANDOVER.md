# Phase 8V-P7 — Final Artifact and Physical Closure Handover

## Status: CONDITIONAL PASS continues. Final tag NOT created.

## What this session was

A continuation of Phase 8's commercial-licensing closure effort, focused on: (1) discovering and
closing a real artifact-staleness gap Phase 8V-P6 left open (Windows executables running pre-fix code),
(2) aligning all four product artifact families to a new immutable version, (3) completing Scenario 2
(Retail late renewal) and Scenarios 6/7 (device replacement, plan downgrade) that had never been
reached in any prior session.

## Headline real results

- Found, by direct file-timestamp comparison, that both Windows executables predated the Phase 8V-P6
  commercial-enforcement fix by 4 real days -- corrected Phase 8V-P6's own "Windows rebuild not
  required" conclusion, rebuilt both.
- All 8 canonical version sources aligned `1.0.0-rc.3 -> 1.0.0-rc.4` (Android versionCode `4 -> 5`) per
  the project's own documented policy, with zero rc.3 history overwritten.
- Both Android products upgraded in place on the real physical device; certificate/identity continuity
  reconfirmed exactly.
- Scenario 3 physically re-proven on the rebuilt Clinic artifact.
- Scenario 2's restriction half physically proven for Retail for the first time, using the same
  unconfounded method already proven for Clinic; a real late renewal was applied through the real Owner
  service pipeline.
- **Scenario 6 reached a full, real PASS** -- discovered mid-session that genuine multi-instance Windows
  product identities (same machine, distinct app-data directories, each with a real device keypair via
  the real activation contract) satisfy the governing spec's own definition of a legitimate client
  identity, correcting an earlier overly pessimistic "no VM/Sandbox available" disclosure.
- **Scenario 7's Owner-side mechanics reached a full, real PASS**, including a genuine new finding about
  how the device-limit reconciliation scan's date-precision evaluation interacts with short-duration
  temporary exceptions.
- A real mid-session bug (wrong Flask config key name used for the license-key HMAC pepper) was found
  by inspecting real captured wire evidence and fixed within the same session.

## Why the tag remains withheld

An extended, real, disclosed physical Android device disconnection partway through the session (after
Scenario 2's restriction half and Scenario 3's smoke check were already complete) blocked: Scenario 2's
renewal-restoration/88.00/return confirmation, stale-assertion rejection, both products'
backup/restore/export, Clinic invoice/payment integrity, Scenario 7's device-facing confirmation, and a
systematic Logcat/data-preservation sweep. Multiple real recovery attempts (`adb kill-server`/
`start-server`, repeated rechecks over an extended period) did not restore the connection within this
session's remaining time. Zero P0, zero P1 remain; one carried P2 (local-deactivation UX) does not
block per the governing spec's own instruction.

## Standing rules unchanged

Original `AuraEnterprise` repo: read-only, always (confirmed untouched this session too). No Phase 9
work. No fabricated physical evidence -- every PASS claimed in this session's docs is backed by real,
inspectable evidence (wire captures, direct DB queries, real product processes); every gap is disclosed
plainly rather than assumed complete. Synthetic data only. No Android signing-key regeneration. No
destructive git operations. Never move the conditional tag.

## Next session priority

Reconnect the device first; verify stability before any other work. Then: Scenario 2's remaining half
(the Owner-side renewal is already real and applied -- only the on-device confirmation, 88.00, and
return cases remain), backup/restore/export (entry points already located on both products), Clinic
invoice/payment, stale-assertion (needs the capture middleware extended with hold/release), and
Scenario 7's device-facing assertion confirmation.
