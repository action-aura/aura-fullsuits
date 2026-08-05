# External Workspace Entry Fingerprints (M10 Entry)

Real, executed fingerprints at the exact start of M10. Unified Mobile
branch `feat/retail-unified-mobile-android-ios`, real full HEAD at
capture time: `a677e5666c18b2a96595d4bec8dd7a554fb236fd` — matches the
accepted M9 HEAD exactly.

Accepted M9 baselines, carried forward as the real starting numbers
this milestone measures against:
- Shared tests: **631**
- Retail Python: **194** (not independently re-verified this session;
  same disclosed pattern as M7-M9)
- Android APK: builds (`androidApp-debug.apk`, confirmed at M9 close)

## Real, current toolchain versions (from source, not memory)

- Kotlin: `2.0.21` (`mobile/aura-retail-unified/build.gradle.kts:5-9`,
  pinned to match `android/aura-retail`'s own existing compiler
  version)
- Compose Multiplatform: `1.7.0` (`build.gradle.kts:12`)
- Android Gradle Plugin: `8.5.2` (`build.gradle.kts:10-11`)
- SQLDelight: `2.3.2` (`build.gradle.kts:13`)
- Android `minSdk`: `26` (`shared/build.gradle.kts:131`)
- Android `compileSdk`: `35` (`shared/build.gradle.kts:129`)
- iOS source-set structure: `iosX64()`/`iosArm64()`/
  `iosSimulatorArm64()` declared unconditionally in
  `shared/build.gradle.kts:29-31`; on this Windows host, Kotlin's
  Default Hierarchy Template does not materialize a real `iosMain`
  Gradle source set (`kotlin.native.ignoreDisabledTargets=true`,
  `gradle.properties`) — real `iosMain`/`iosTest` directories exist on
  disk (`shared/src/iosMain/`, `shared/src/iosTest/`, containing the
  real M9 `SecureRandomBytes.ios.kt`) but are not compiled here,
  unchanged standing constraint since M4.

## `C:\Users\Dell\Desktop\AuraEnterprise\aura-fullsuits-phase9r`

```
HEAD: 8c35768393d9407f72ef34aa93705f9c454453f7   (unchanged since M6-M9)
STATUS / DIFF_BINARY_HASH / DIFF_CACHED_BINARY_HASH / UNTRACKED_LIST_HASH:
  e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85 (empty, all)
```

## `C:\Users\Dell\Desktop\AuraEnterprise\AuraEnterprise` (legacy repo)

```
HEAD: 414e6ea5ca9fc698fe075a4ae4464cebf0b7bf34   (unchanged since M6-M9)
STATUS: 22 modified, 15 untracked (byte-identical to M9 exit)
DIFFSTAT: 22 files changed, 1441 insertions(+), 260 deletions(-)
DIFF_BINARY_HASH:        cc37af7f7c95450e59be66bb4785a67bdfaeae18f7c86bbc2e3161ab76c66f7
DIFF_CACHED_BINARY_HASH: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85
UNTRACKED_LIST_HASH:     cafdb44a28f4865453bf28d1def21c1ca34496052ed638da5882a776bb4afe9f
```

## `C:\Users\Dell\Desktop\AuraEnterprise\aura-fullsuits-owner-ui` (Owner UI-modernization worktree)

```
HEAD: f59b021922a6559183e3a829f74594e17ab79e41   (unchanged since M9 exit)
STATUS: 0 modified, 1 untracked (var/)
DIFF_BINARY_HASH / DIFF_CACHED_BINARY_HASH:
  e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85 (empty, both)
UNTRACKED_LIST_HASH: 8e09224e2b00f21a2bcc85d6e0c2ea5d447af10ab6bfaad5bb731ba4b71ec7e
```

Real, byte-identical to the M9 exit capture — no further drift since
that milestone's own investigated, fully-attributed change.

## What "unchanged" will mean at M10 exit

Same 8-command protocol, including literal sorted untracked path
lists, re-run against all three workspaces at M10 exit. Required
guarantee: `NO_M10_ATTRIBUTABLE_EXTERNAL_WORKSPACE_CHANGE` — this
initiative's own commands must never be the cause of a change in any
of the three; an independently-active worktree changing for its own,
unrelated, real reasons (as `aura-fullsuits-owner-ui` did during M9)
is not itself a violation, provided it is investigated and honestly
attributed, not silently claimed unchanged or silently omitted.
