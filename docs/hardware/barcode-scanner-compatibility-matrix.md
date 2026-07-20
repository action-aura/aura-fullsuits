# Barcode Scanner Compatibility Matrix

Status definitions used throughout: **PHYSICALLY VERIFIED** (tested with real hardware), **PROTOCOL SUPPORTED** (implemented, code-reviewed and/or unit-tested, no physical hardware available this wave), **SIMULATED** (tested via synthetic/injected events only), **ADAPTER PREPARED** (contract exists, not implemented), **NOT IMPLEMENTED**, **REQUIRES VENDOR SDK**.

| Input class | Retail Windows | Retail Android | Clinic |
|---|---|---|---|
| Phone camera (CameraX + ML Kit) | n/a | **PHYSICALLY VERIFIED** (Wave 1A) | Not a Clinic feature — retail-only, by design |
| USB HID keyboard-mode scanner | **PROTOCOL SUPPORTED** (real implementation, pre-existing, code-reviewed this wave) | **PROTOCOL SUPPORTED, unit-tested** (new this wave, `HidScanDetectorTest.kt`, 7/7 passing) | n/a |
| Bluetooth HID keyboard-mode scanner | **PROTOCOL SUPPORTED** (same code path as USB HID — indistinguishable at the OS level) | **PROTOCOL SUPPORTED, unit-tested** (same code path as USB-OTG HID) | n/a |
| USB Serial/COM scanner | **ADAPTER PREPARED** | n/a | n/a |
| Bluetooth SPP/BLE scanner | n/a | **ADAPTER PREPARED** | n/a |
| Android intent-based scanner | n/a | **NOT IMPLEMENTED** | n/a |
| Vendor SDK (e.g. Zebra DataWedge) | **REQUIRES VENDOR SDK** | **REQUIRES VENDOR SDK** | n/a |

## Commercial-wording guidance
Approved: *"Supports standard USB and Bluetooth keyboard-mode barcode scanners, plus camera scanning on Android. Specialized devices may require an adapter."*

Not approved, and not used anywhere in this project's docs or UI: *"supports every barcode scanner"* / any unqualified universal-compatibility claim.

## Why "PROTOCOL SUPPORTED" and not "PHYSICALLY VERIFIED" for HID
No physical USB/Bluetooth HID barcode scanner hardware was available during Wave 1B (confirmed with the project owner before proceeding). The underlying keystroke-burst-timing algorithm is unit-tested with high confidence (Windows: proven in production use predating this wave; Android: 7 new unit tests covering fast-burst recognition, slow-typing rejection, mid-sequence pause handling, minimum-length rejection, empty-buffer Enter, multi-scan reuse, and explicit reset) — but a unit test cannot prove real hardware timing characteristics match the simulated timestamps used in the tests, nor can it prove OS-level HID driver behavior across the range of real scanner models. This gap is explicitly tracked in the residual risk register, not glossed over.

## Real device regression check
Retail Android's existing camera-scanner path (Part L/M's baseline) was re-confirmed working after this wave's `MainActivity.dispatchKeyEvent` addition — a real device pass through POS (search, tap-to-add, checkout) showed no interference from the new key-event listener with normal touch/typing interaction.
