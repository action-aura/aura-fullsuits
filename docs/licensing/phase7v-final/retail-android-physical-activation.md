# Phase 7V-F — Retail Android Physical Activation (Part H)

## Status: NOT VERIFIED — device disconnected before this part could begin

No Retail Android physical activation was attempted (device unavailable — see
`retail-android-physical-upgrade.md` for the exact sequencing).

## What stands in as evidence

- Retail's shared licensing core (identical Python package to Clinic's) was proven live on Windows
  through the complete activation→check-in→offline→WARNING→RESTRICTED lifecycle, including the
  real P0 trusted-time fix — see `windows-live-restricted-mode-closure.md`.
- The authoritative financial scenario (100.00 − 20.00 discount, ×10% tax = 88.00) was re-confirmed
  directly against `core/retail/pricing.py` this session, and separately via the live Windows
  Retail exe in `RESTRICTED` state (proving the calculation is unaffected by license state) —
  camera/HID/receipt-share regressions were not claimed or tested, per the spec's own instruction
  not to claim hardware compatibility without a connected, tested device/scanner.

## Verdict

**NOT VERIFIED** (physical). Shared core + financial calculation: **PASS** (proven live, Windows
+ direct verification).
