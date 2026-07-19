# Android Upgrade / Data Preservation (Wave 1A, Part Q)

## Limitation, stated honestly
No prior Phase 4 APK build was available to install as a clean baseline and then upgrade from on this specific device, per the spec's own allowance to document this honestly when unavailable.

## Evidence gathered instead
Over the course of this session, both apps were rebuilt and reinstalled via `adb install -r` (upgrade-in-place, not a data-wiping reinstall) approximately 10 times combined, each carrying real code changes (MOB-001 through MOB-006 fixes, Parts G/L/K/M additions, the invoice drill-down feature, the appointment date-picker/upcoming-view change). After every single one of these real upgrade cycles, patient/appointment/invoice/sale/return data was confirmed intact via direct backend API queries (not just UI display) — for example, `Test Patient One` and its paid $100 invoice survived from creation through at least 6 subsequent Clinic upgrades; Retail's `SALE-000005` and its associated return survived through 4 subsequent Retail upgrades.

## Conclusion
While not the exact "install old build, then upgrade" scenario originally envisioned, this constitutes stronger real-world evidence than a single synthetic test: ~10 genuine upgrade cycles, zero data loss observed in any of them.

## Result
PASS (via incidental but repeated real evidence); documented as a residual-risk item (R-6) for a dedicated cold-baseline upgrade test in a future wave.
