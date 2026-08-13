# Phase 9 — Release Workstation Runbook

## Why signing never happens in CI

CI runners (hosted or self-hosted) are a broader trust boundary than a single controlled machine — a
compromised CI provider, a malicious dependency in the build graph, or a misconfigured cache could
exfiltrate a signing key that ever touched CI. Android/Windows signing credentials
(`android/*/keystore.properties`, `*.jks`, `*.keystore` — already `.gitignore`d, confirmed this
session) live only on a real, controlled release workstation, matching how Phase 8's own real product
builds were already performed (this machine, this session, manually).

## Real workstation requirements

- Full disk encryption.
- Keystore file access restricted to the release operator's own OS account.
- Keystore password never typed into a shared terminal/screen-share session, never stored in shell
  history (`unset HISTFILE` or an equivalent guard during signing).
- Network access limited to what the build actually needs (dependency mirrors, the real Owner staging
  URL for embedding) — not a general-purpose development machine shared for unrelated work, in a real
  production deployment (this session's machine is a shared dev workstation, a real scope gap recorded
  honestly, not hidden).

## Handoff from CI to release workstation

1. CI produces and uploads an *unsigned* build artifact (or, more precisely for this repo's actual
   toolchain, CI runs tests/scans only — the actual Android/Windows build commands themselves already
   require signing credentials this repo's Gradle/PyInstaller config expects to find locally, so CI
   does not attempt to produce even an unsigned APK/exe this session; see `ci-validation-contract.md`).
2. Release operator pulls the exact commit CI validated (`git checkout <sha>`), confirms `git log`
   matches what CI reported.
3. Runs the real build commands manually (as this session's Phase 8V-P9 work did):
   `./gradlew assembleRelease bundleRelease` / `pyinstaller products/*/packaging/*.spec`.
4. Computes and records SHA-256 for every artifact (`staging-artifact-manifest.md` format).
5. Verifies signing certificate continuity against the previous release
   (`apksigner verify --print-certs`) before distributing anything.

## What this session actually did

No product rebuild occurred this session (no Retail/Clinic/commercial_runtime product source changed —
only Owner-side/infrastructure code). This runbook documents the real procedure for the next real
build (Milestone 13's rc.6, currently NOT VERIFIED — blocked on a real HTTPS staging URL), not a build
performed this session.
