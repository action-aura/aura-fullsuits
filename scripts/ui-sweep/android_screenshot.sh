#!/bin/sh
# Capture on the device then pull.
#
# Two traps hit here, both worth keeping written down:
#   * `adb exec-out screencap -p > file` through this shell produced 0 bytes.
#   * Git Bash rewrites a leading /sdcard/... into C:/Program Files/Git/sdcard/...
#     (MSYS path conversion), so the device path must be protected.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL="*"
ADB="F:/Android/Sdk/platform-tools/adb.exe"
OUT="C:/Users/MSI/.claude/jobs/b602c1c7/tmp/android-shots"
NAME="${1:-shot}"
mkdir -p "$OUT"
rm -f "$OUT/$NAME.png"
"$ADB" shell screencap -p /sdcard/_shot.png
"$ADB" pull /sdcard/_shot.png "$OUT/$NAME.png" 2>&1 | tail -1
"$ADB" shell rm -f /sdcard/_shot.png
wc -c < "$OUT/$NAME.png"
