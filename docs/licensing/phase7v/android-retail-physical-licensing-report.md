# Phase 7V — Android Retail Physical Licensing Validation (Part M)

## Status: NOT VERIFIED — no physical device connected

Same gate as Parts K/L. None of the spec's Part M steps (launch, activation, financial-authority
sale test on-device, offline/restricted matrix, barcode scan regression, receipt-share regression,
HID input regression, suspend/reactivate, deactivate, Logcat/network inspection) were performed on
real Android hardware this session.

## What was proven instead (non-physical, this session)

- **Authoritative sale calculation** re-confirmed directly (`windows-rc1-to-rc2-installer-validation.md`):
  `calculate_invoice(subtotal=100.00, discount_amount=20.00, tax_rate_pct=10.0)` →
  `{'tax': 8.0, 'total': 88.0}`, exact match to spec. This is the same `core/retail/pricing.py`
  module Android's embedded Python also runs — pure arithmetic, no platform-specific surface area.
- Full activate → deactivate licensing lifecycle proven live on Windows against a real Owner
  (`windows-rc1-to-rc2-installer-validation.md`), using the identical shared Python licensing core.
- Retail's Android release build succeeded (`android-rc2-signed-build-report.md`), including
  `testReleaseUnitTest` passing.

## Explicitly not claimed

Camera barcode scanning, external HID scanner compatibility, and receipt-sharing regressions are
**not claimed as verified** in this session — per the spec's own instruction not to claim physical
hardware compatibility without a connected, tested device.

## Verdict

**NOT VERIFIED** (physical). Financial-calculation correctness and shared-core licensing lifecycle:
PASS (via equivalent Windows live testing + direct calculation re-confirmation). This gap is a
mandatory Definition-of-Done item — its absence withholds the final Phase 7V closing tag.
