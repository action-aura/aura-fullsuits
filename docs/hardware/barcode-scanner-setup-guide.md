# Barcode Scanner Setup Guide

## Windows (Retail)
1. Plug in a USB HID keyboard-mode scanner (or pair a Bluetooth HID scanner the same way you'd pair a Bluetooth keyboard) — no driver install needed; it behaves exactly like a keyboard to Windows.
2. In Aura Retail, go to **Settings → Barcode Scanner**.
3. Click **Start Test**, then scan any barcode. The captured value appears in the test box immediately if recognized.
4. Adjust **Configuration** if your specific scanner needs it:
   - **Timeout (ms)**: how fast consecutive keystrokes must arrive to be recognized as a scan rather than typing. Increase if your scanner is slower than default and scans aren't being recognized; decrease if very fast human typing is being misidentified as a scan (rare).
   - **Minimum length**: barcodes shorter than this are ignored (guards against accidental key mashes).
   - **Prefix / Suffix**: some scanners can be configured (via their own setup barcodes, see your scanner's manual) to wrap the code in extra characters — set these to match if so; leave blank otherwise.
5. Scanning works automatically anywhere in POS, Products, and Purchase Orders once configured — no need to click into a field first, except when explicitly capturing into a single barcode field (e.g. editing a product), where an on-screen **Scan** button arms one-shot capture.

**Note**: because a HID scanner looks exactly like a keyboard, the app cannot tell you whether one is physically connected — "Scanner Status" reflects recent scan activity, not a hardware connection check.

## Android (Retail)
Two independent ways to scan, both feed the same cart:

1. **Phone camera** — tap the scan icon in POS; grant camera permission if prompted. Physically verified working (Wave 1A).
2. **USB-OTG or Bluetooth HID scanner** — connect via a USB-OTG adapter, or pair via Bluetooth exactly like a Bluetooth keyboard. No in-app configuration screen exists yet for Android (Windows-style Settings → Barcode Scanner is Windows-only this wave) — it uses fixed defaults (50ms timeout, 3-character minimum) matching the Windows engine's own defaults. Not tested against real HID hardware this wave (see the compatibility matrix) — if you have a real scanner and it doesn't work as expected, that's exactly the kind of real-world gap this documentation exists to surface honestly, not hide.

## Not yet supported (any platform)
Serial/COM scanners, Bluetooth SPP/BLE (non-HID) scanners, vendor SDK integrations (e.g. Zebra DataWedge). Contracts exist for these (see `docs/hardware/barcode-input-architecture.md` Part N) but no implementation — do not purchase this class of hardware expecting it to work with Aura Retail today.
