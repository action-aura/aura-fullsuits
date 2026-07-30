# Phase 8V-P4 — Final Artifact Verification

## Certificate continuity -- full SHA-256, not abbreviated, both PASS

```
$ apksigner verify --print-certs AuraClinic-1.0.0-rc.3-urlconfigured.apk
Signer #1 certificate DN: CN=Action Aura, OU=Aura Clinic, O=Action Aura, L=Amman, ST=Amman, C=JO
Signer #1 certificate SHA-256 digest: 35508048cee7776ca94a45a9f04c6a870edf98729429482a8ad44e89dd0bbbf2

$ apksigner verify --print-certs AuraRetail-1.0.0-rc.3-urlconfigured.apk
Signer #1 certificate DN: CN=Action Aura, OU=Aura Retail, O=Action Aura, L=Amman, ST=Amman, C=JO
Signer #1 certificate SHA-256 digest: cae6b18450c14a71eba47545e5b3ba52089eef0397d5881f4c1dc7d5a4b7d32d
```

Both match the historical production signing identity exactly (same values as every prior rc build
this project has verified). Neither is the AOSP debug certificate. Upgrade continuity preserved --
safe to install as an in-place upgrade over any prior signed Clinic/Retail install.

## Non-debuggable, correct package/version

```
$ aapt dump badging AuraClinic-1.0.0-rc.3-urlconfigured.apk | grep -i "application-debuggable\|package:"
package: name='com.actionaura.clinic' versionCode='4' versionName='1.0.0-rc.3' ...
$ aapt dump badging AuraRetail-1.0.0-rc.3-urlconfigured.apk | grep -i "application-debuggable\|package:"
package: name='com.actionaura.retail' versionCode='4' versionName='1.0.0-rc.3' ...
```

No `application-debuggable` line in either dump (aapt only emits it when `android:debuggable="true"`
is present) -- confirms non-debuggable release builds.

## Full per-artifact record

| Field | Clinic | Retail |
|---|---|---|
| Filename (APK) | `AuraClinic-1.0.0-rc.3-urlconfigured.apk` | `AuraRetail-1.0.0-rc.3-urlconfigured.apk` |
| SHA-256 (APK) | `d497c226802efd50dd1d952f70e61da5ee78509673b27a35f38f57f541752538` | `c7b464ca7c653dd465f3134fd7c3e24e3eb67ae38685d6c427bbb5e322ce844b` |
| Size (APK) | 54,159,013 bytes | 67,753,285 bytes |
| Filename (AAB) | `AuraClinic-1.0.0-rc.3-urlconfigured.aab` | `AuraRetail-1.0.0-rc.3-urlconfigured.aab` |
| SHA-256 (AAB) | `70a8fa5eae20d49ea988a2ec7770b5880c80289248b144f111caae9b31d53cc2` | `449b280657fb9eadda2dbb4435e01b040b9ed4e0e3da22878bfe332d689dcea6` |
| Size (AAB) | 34,901,422 bytes | 41,771,454 bytes |
| Package ID | `com.actionaura.clinic` | `com.actionaura.retail` |
| versionName | `1.0.0-rc.3` | `1.0.0-rc.3` |
| versionCode | `4` | `4` |
| Certificate SHA-256 | `35508048cee7776ca94a45a9f04c6a870edf98729429482a8ad44e89dd0bbbf2` | `cae6b18450c14a71eba47545e5b3ba52089eef0397d5881f4c1dc7d5a4b7d32d` |
| Debuggable | No | No |
| Git commit | `ab774e5` (+ this session's docs-only commits, no source change) | same |
| Configured Owner URL | `http://127.0.0.1:5551/api/licensing/v1` | same |
| Resolved licensing endpoint | Confirmed reachable from host; device-side confirmed via real activation in `initial-physical-activation.md` | same |
| Trust anchor / contract / assertion version | Unchanged since Phase 8V-P2 (no source change) | same |

## Result: certificate continuity **PASS** for both products. Safe to install/upgrade physically.
