# Wave 1A — Device & Environment Discovery

Status: **PROVEN**. Real `adb` output, captured 2026-07-17, this environment.

## adb / SDK

```
$ adb version
Android Debug Bridge version 1.0.41
Version 37.0.0-14910828
Installed as C:\Users\Dell\AppData\Local\Android\Sdk\platform-tools\adb.exe
```

No `emulator` package installed under the SDK — irrelevant this wave, a
real physical device is connected.

## Connected device

```
$ adb devices -l
1122070476060894    device product:X6528-OP model:Infinix_X6528 device:Infinix-X6528 transport_id:2
```

**Status: `device`** (authorized, not `unauthorized`/`offline`) — a valid
physical test target per this phase's explicit requirement. **Exactly one
device connected** — no ambiguity about which device `adb` commands target.

| Property | Value |
|---|---|
| Model | Infinix X6528 |
| Manufacturer / Brand | INFINIX / Infinix |
| Android version | 13 |
| API level | 33 |
| CPU ABI | arm64-v8a |
| Screen resolution | 720×1612 |
| Screen density | 320 dpi |
| Available storage (`/data`) | 52,880,684 KB free of 116,702,208 KB total (~55% used, ~50 GB free) |
| RAM | 7,958,780 KB total, 3,170,876 KB available |
| USB debugging | Enabled (`adb_enabled=1`, confirmed working) |
| Battery | USB-powered, charging, present |
| System locale | `en-US` |
| System theme | Dark mode ON (`Night mode: yes`) at discovery time |
| Background process limit | Not restricted (`null` = default) |
| Security patch | 2025-10-01 |
| Build fingerprint | `Infinix/X6528-OP/Infinix-X6528:13/TP1A.220624.014/250917V1996:user/release-keys` |

## Pre-existing installs

```
$ adb shell pm list packages | grep -i actionaura
(no output)
```

Neither `com.actionaura.retail` nor `com.actionaura.clinic` (nor any
`.debug`/`.staging` variant) is installed — this device starts clean for
this wave. No prior Phase 4 APK is present on this device to test upgrade
-install against (see `android-upgrade-data-preservation-report.md` for
how this is handled honestly).

## Conclusion

A real, authorized, single physical Android device is available. Wave 1A
proceeds with genuine on-device validation — this is not a build-only
substitute.
