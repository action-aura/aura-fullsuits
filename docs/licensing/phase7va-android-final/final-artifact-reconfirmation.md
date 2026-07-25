# Phase 7V-A — Final Artifact Reconfirmation (Part R)

Supersedes `docs/licensing/phase7v-final/final-artifact-reconfirmation.md`'s Android checksums.
These are the truly-final Clinic/Retail Android artifacts: rebuilt after all six fixes in
`bugs-found-and-fixed.md` (including the Part I offline-lifecycle fix), with the temporary
`PHASE7VADIAG5` diagnostic print removed, `testReleaseUnitTest` + `lintRelease` both green, and
`assembleRelease` + `bundleRelease` both green for both products.

## Android

| Artifact | SHA-256 |
|---|---|
| Clinic APK (`app-release.apk`) | `493a846ab628ee94b2faf48d50518261c93788f2b51e10c09ee57ed690234b58` |
| Clinic AAB (`app-release.aab`) | `cf392ccb8038411305a3b16c95767ea9c3da0adffabec3d1f74933738ff9c290` |
| Retail APK (`app-release.apk`) | `a7b3d10f224202bf5a2b8cd29430344de8db13ec97c2061f234d71c20531219b` |
| Retail AAB (`app-release.aab`) | `1b9d570e7f04b8a2e1a29e473ae2031912323b56b7ef28fecca99d96360bc98d` |

Both built with `-PownerLicensingBaseUrl=http://127.0.0.1:19101` (validation-only; a local test
Owner instance, not a real production endpoint — matches the same pattern used throughout Phase
7V, not a new departure).

## Certificate continuity — reconfirmed

`apksigner verify --print-certs` on both final APKs:

- Clinic: `CN=Action Aura, OU=Aura Clinic` — SHA-256 `35508048cee7776ca94a45a9f04c6a870edf98729429482a8ad44e89dd0bbbf2`
- Retail: `CN=Action Aura, OU=Aura Retail` — SHA-256 `cae6b18450c14a71eba47545e5b3ba52089eef0397d5881f4c1dc7d5a4b7d32d`

Both exactly match `rc2-release-candidate-manifest.md`'s original rc.1-continuity record and every
earlier rc.2 build this whole Phase 7V effort. No production signing key was regenerated at any
point.

## Reconfirmed for this final round

- Package IDs unchanged (`com.actionaura.clinic`, `com.actionaura.retail`).
- `versionName "1.0.0-rc.2"`, `versionCode 3` — unchanged.
- `network_security_config.xml` unchanged and reviewed: cleartext permitted only to
  `127.0.0.1`/`localhost`, `base-config cleartextTrafficPermitted="false"` for everything else. No
  weakening at any point this session, including during the LAN-detour investigation (which was
  abandoned specifically to avoid needing this).
- No debug build, no test bypass, no synthetic-data shortcut baked into either artifact.
- No secrets in logcat across the full physical test session (see `final-decision.md`'s Part O).

## Not overwritten

rc.1 artifacts remain untouched, as in every earlier round.
