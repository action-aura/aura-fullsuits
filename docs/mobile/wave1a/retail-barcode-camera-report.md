# Retail — Barcode Scanner / CameraX + ML Kit (Wave 1A, Part F)

## Test
Mandatory physical-device validation (cannot be simulated in an emulator or faked via build-only checks). On-device: opened Products, invoked the barcode/scan control, pointed the real camera at a real physical barcode.

## Result
User-confirmed: **works perfectly** — camera permission granted, live camera preview rendered, barcode recognized and the scanned value populated correctly.

## Notes
No crash, no permission-flow issue, no ML Kit initialization failure observed. This is the one Wave 1A item that explicitly could not be validated any other way than on real hardware, and was exercised as such.
