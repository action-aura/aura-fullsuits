# Phase 8V-P2 — Final Android Artifact Evidence

Stored at `dist/android/{clinic,retail}/` (gitignored, local-only, same convention as
`dist/installers/`'s Windows artifacts -- not present on a fresh clone; rebuild via the commands in
`android-rc3-build-report.md` if needed on another machine).

| Field | Clinic | Retail |
|---|---|---|
| Package ID | `com.actionaura.clinic` | `com.actionaura.retail` |
| versionName | `1.0.0-rc.3` | `1.0.0-rc.3` |
| versionCode | `4` | `4` |
| APK file | `AuraClinic-1.0.0-rc.3.apk` | `AuraRetail-1.0.0-rc.3.apk` |
| APK SHA-256 | `daaaaaa30fe6630fa10d38c3b1ec951eca70f123cbd8b57c7f70a3f427be5f50` | `3746fdff94672f80500c94abd11513a0a22dafd97b5b0ffef1322ab87cf647d6` |
| APK size | 54,159,013 bytes | 67,753,285 bytes |
| AAB file | `AuraClinic-1.0.0-rc.3.aab` | `AuraRetail-1.0.0-rc.3.aab` |
| AAB SHA-256 | `c5453af7e8f6ecff379163c40cf08a2ce1c49c0c99a270afbb3db81701ac4240` | `b0eb43e32bf0ff39135e229c8c213970c4be35535ce58e85c6152d4784836c9c` |
| AAB size | 34,901,602 bytes | 41,771,366 bytes |
| Certificate SHA-256 | `35508048cee7776ca94a45a9f04c6a870edf98729429482a8ad44e89dd0bbbf2` | `cae6b18450c14a71eba47545e5b3ba52089eef0397d5881f4c1dc7d5a4b7d32d` |
| Debuggable | No | No |
| Owner URL (source, unchanged) | `/api/licensing/v1` | `/api/licensing/v1` |
| Trust anchor (source, unchanged) | bundled `commercial_runtime/licensing_contracts/trust_anchor.json` | same |
| Build timestamp | 2026-07-30 19:34 local | 2026-07-30 19:39 local |
| Physical-device validation | **Not performed** -- no device connected this session | **Not performed** |

## Inspection for forbidden content

Neither Android module's source changed this session (the Scenario 7 fix and preflight command are
Owner-only Python/Flask changes under `owner/`). The rc.3 artifacts therefore carry forward exactly
the same product code, trust anchor, and commercial contract as every prior rc.3-source-tagged build
-- no missing `/api/licensing/v1`, no stale trust anchor, no debug configuration (confirmed via
badging dump and non-debug signer identity), no TLS bypass, no HTTP production endpoint, no fake
paid state, no hidden license bypass, no fake clock, no secrets/keystore/signing passwords/synthetic
customer records embedded (none of this session's work touched product source or assets), no Phase 9
configuration (none exists anywhere in this codebase).
