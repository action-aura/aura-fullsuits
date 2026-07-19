# Physical Device Performance Observations (Wave 1A, Part R)

Device: Infinix X6528, Android 13, arm64-v8a, 7.9GB RAM. All measurements taken via `adb` on the real device.

## APK sizes (debug)
- Retail: ~67 MB
- Clinic: ~54 MB

(Both embed a full Chaquopy Python runtime + interpreter, which dominates size — expected for this architecture.)

## Cold-start time (force-stop → embedded backend socket listening)
- Clinic: ~3.4s
- Retail: ~4.7s

This measures Activity display *plus* Python interpreter boot *plus* Flask/waitress server bind — i.e. genuine "app becomes usable" time, not just the ~800ms Activity-display time reported by `adb shell am start -W`.

## Memory footprint (steady state, logged in, idle)
- Clinic: TOTAL PSS ~124 MB
- Retail: TOTAL PSS ~180 MB

Both comfortably within headroom on a 7.9GB-RAM device; not flagged as a concern here, but noted for lower-RAM device testing in a future wave.

## Qualitative observations
- No jank or dropped-frame complaints reported by the tester across any screen.
- The device's own background-process aggressiveness (Transsion/XOS) was the dominant environmental factor observed, not the apps' own resource usage — see the residual risk register (R-3).
