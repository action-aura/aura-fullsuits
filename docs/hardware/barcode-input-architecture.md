# Barcode Input Architecture (Wave 1B, Part L)

## Principle
The POS workflow must not care whether a scanned code came from a camera, a USB/Bluetooth keyboard-wedge scanner, manual typing, or (in the future) a Serial/BLE/vendor-SDK adapter. Every input class normalizes to the same thing: **a decoded string handed to the same lookup+add-to-cart logic**.

This was **discovered already substantially built**, not designed from scratch this wave — the Windows desktop frontend (`products/retail/frontend/subsystem-retail.js`, "BARCODE SCANNER ENGINE" section) already had a complete, production-quality HID keyboard-wedge implementation predating this wave, with no prior documentation describing it as such. This wave's real contribution was: discovering and documenting it, porting its proven algorithm to Android (which had none), and wiring both platforms' scan sources into their existing single "add to cart" code path so the normalization principle is genuinely enforced, not just described.

## Supported input classes and their current status

| # | Class | Platform | Status |
|---|---|---|---|
| 1 | CameraX + ML Kit | Android | **PHYSICALLY VERIFIED** (Wave 1A, real device, real barcode) |
| 2 | USB HID keyboard-mode scanner | Windows | **PROTOCOL SUPPORTED** — real, working implementation (burst-timing detection, focus-aware, configurable), code-reviewed this wave; not tested against real scanner hardware (none available) |
| 3 | Bluetooth HID keyboard-mode scanner | Windows | **PROTOCOL SUPPORTED** — identical to #2; a Bluetooth HID scanner is indistinguishable from a USB one at the OS/keyboard-event level, so the same code path covers both |
| 4 | USB-OTG HID keyboard-mode scanner | Android | **PROTOCOL SUPPORTED, unit-tested, NOT physically verified** — new this wave (`HidScanDetector.kt`, ported from the Windows engine's proven algorithm), wired into `MainActivity.dispatchKeyEvent` and `PosScreen`. No physical HID scanner hardware was available to test against. |
| 5 | Bluetooth HID keyboard-mode scanner | Android | Same code path as #4 (HID is HID regardless of transport) — same status |
| 6 | USB Serial/COM scanner | Windows | **ADAPTER PREPARED** (contract only, see Part N) — not implemented |
| 7 | Bluetooth SPP/BLE scanner | Android | **ADAPTER PREPARED** (contract only, see Part N) — not implemented |
| 8 | Android intent-based scanner | Android | **NOT IMPLEMENTED** — deferred, no current demand identified |
| 9 | Vendor SDK scanner (e.g. Zebra DataWedge) | Both | **REQUIRES VENDOR SDK** — deferred, contract only (Part N) |

## The normalized event, concretely

**Windows** (`subsystem-retail.js`): `_dispatchScan(code, cfg)` is the single entry point every input class funnels into. It routes to whichever screen/modal is currently active (`_posScan`, `_productsScan`, `_poScan`, or a captured one-shot field like the product-edit barcode input), all of which share `_findByCode()`.

**Android** (new this wave): `HidScanBus.lastScan` (a `ScanEvent(code, seq)`) is observed by `PosScreen` via a `LaunchedEffect`, using the *exact same* `findProductByCode()` + `addOne()` calls that `BarcodeScannerDialog`'s `onResult` callback (the camera path) already used. The two input sources are literally two different `LaunchedEffect` blocks feeding the same downstream logic — not two parallel, divergent implementations.

## Duplicate suppression
- Windows: tracks `lastScanAt`/`lastScanCode`, exposed to the settings/status UI (not currently used to hard-block a repeat scan — a cashier scanning the same item twice for quantity 2 is legitimate and must not be suppressed).
- Android: `BarcodeDebounce.kt`'s `isDuplicateScan()` (pre-existing, Phase 4F) suppresses the *same code within 1500ms* for the camera's continuous-scan mode specifically (rapid-fire duplicate frames from the same physical barcode still in view) — a deliberately narrower, continuous-video-specific problem than "the cashier scanned twice on purpose," which is why HID scans don't reuse this debounce (a HID scan is one discrete Enter-terminated event, not a video frame stream).

## Known/unknown barcode flow
Both platforms: a matched code adds to cart (or fills a target field, depending on which screen is active); an unmatched code surfaces a real "not found" flow — Windows shows a modal with Add New Product / Scan Again / Cancel; Android's camera path already showed an inline `lastScanned = "✗ Not found: $code"` status (Wave 1A), which the new HID path reuses verbatim.
