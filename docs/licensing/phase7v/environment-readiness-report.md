# Phase 7V — Environment Readiness Report

Captured at Phase 7V start, before any toolchain changes.

| Tool | Status | Detail |
|---|---|---|
| Python | Present | 3.11.9 |
| Java | Present | OpenJDK 17.0.19 LTS (Microsoft build) |
| Gradle | Wrapper-only | No global `gradle`; both Android projects pin wrapper to Gradle 8.9 (`gradle-wrapper.properties`) — this is the supported invocation path, not a gap |
| Android SDK | Present | `%LOCALAPPDATA%\Android\Sdk`, platform-tools present |
| ADB | Present | `adb.exe` at `C:\Users\Dell\AppData\Local\Android\Sdk\platform-tools\adb.exe` (not on PATH by default — full path required), version 1.0.41 (37.0.0-14910828) |
| Connected Android device | **None** | `adb devices -l` returned an empty list at baseline. Physical-validation parts (J–N) are blocked until a device is connected and authorized. No emulator/fabricated substitution will be used for the mandatory rows. |
| Inno Setup / ISCC.exe | **Missing** | Not found at either standard install path (`C:\Program Files (x86)\Inno Setup 6\ISCC.exe`, `C:\Program Files\Inno Setup 6\ISCC.exe`), not on PATH. Closed in Part B via winget. |
| PostgreSQL server | Present | 17.10 (Windows), reachable at `localhost:5432` with the existing `aura_owner` / `aura_owner_dev` dev credentials |
| pg_dump | Present, not on PATH | `C:\Program Files\PostgreSQL\17\bin\pg_dump.exe`, version 17.10 — matches the running server's major version exactly. This is the tool discovery gap Owner's backup/restore tests hit; closed in Part B/C by teaching Owner's backup service to find it via the same standard-install-path search rather than requiring the user's PATH to include it. |
| Android production keystores | Present, outside Git | `C:\Users\Dell\AuraSigningKeys\clinic-release.keystore`, `retail-release.keystore`, plus sibling `.pass` files. Not inspected for content (passwords never printed). `android/aura-clinic/keystore.properties` and `android/aura-retail/keystore.properties` exist locally (gitignored) and are read by both `app/build.gradle` files' existing signing-config block when present. |

## Implication for Phase 7V execution order

1. Part B installs Inno Setup and wires portable `pg_dump` discovery — both are prerequisites for
   Parts C/E/F.
2. Parts H/I (Android signed builds, certificate continuity) do not require a physical device and
   can proceed immediately once the toolchain is closed.
3. Parts J–N (physical device proof) are gated on a device appearing in `adb devices -l`. Per the
   governing spec's Part J instruction, if no device is connected by the time those parts are
   reached, that portion is marked **NOT VERIFIED**, the user is told to connect and authorize a
   device, and the final Phase 7V closing tag is **not created** until physical proof exists.
