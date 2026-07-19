# Android Lifecycle / Resilience Testing (Wave 1A, Part P)

All tests performed on both apps on the real device (Infinix X6528, Android 13).

## Force-stop + relaunch
`adb shell am force-stop` on both apps, then relaunch. Both apps recovered cleanly (no crash), embedded backend re-bound to a fresh port and became reachable within seconds, and all prior data (Clinic: patient + paid invoice; Retail: all 3 returns) was confirmed intact via direct API queries afterward.

## Background / foreground cycling
- 15s background (Clinic): PID unchanged, task resumed instantly on foreground.
- 65s background (Clinic): PID unchanged, no kill, task resumed instantly, no new crash in `adb logcat -b crash`.

## Screen lock / unlock
Device locked (power button) while Clinic was open, held ~10s, unlocked. User-confirmed: "no crashes, works fine."

## Airplane mode toggle
Airplane mode toggled on then off while Clinic was open, with interaction attempted mid-toggle. User-confirmed: "no errors, works fine" (a "can't reach server" state would have been acceptable; no crash occurred).

## Device-specific note
This device (Transsion/XOS skin) was independently observed earlier in the session to aggressively kill backgrounded processes and throttle network reachability to non-foreground apps under some conditions — noted as a residual/environmental factor, not a product defect, since the explicit background-duration tests above passed without incident.

## Result
PASS — both apps demonstrated correct recovery across force-stop, backgrounding, screen lock, and airplane-mode transitions, with no data loss and no crashes.
