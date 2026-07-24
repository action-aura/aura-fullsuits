# Phase 7V — Android Certificate Continuity (Part I)

Compares each product's rc.2 signer certificate (verified via `apksigner verify --print-certs`
against the just-built APKs) with the fingerprint recorded for rc.1 in
`docs/release/wave1b/release-candidate-manifest.md`.

## Clinic

| | rc.1 (recorded) | rc.2 (this session, live) |
|---|---|---|
| Certificate SHA-256 | `35:50:80:...:0B:BB:F2` | `35508048cee7776ca94a45a9f04c6a870edf98729429482a8ad44e89dd0bbbf2` |

Prefix `355080` and suffix `0bbbf2` match exactly. **Same signing identity — continuity confirmed.**

## Retail

| | rc.1 (recorded) | rc.2 (this session, live) |
|---|---|---|
| Certificate SHA-256 | `CA:E6:B1:...:B7:D3:2D` | `cae6b18450c14a71eba47545e5b3ba52089eef0397d5881f4c1dc7d5a4b7d32d` |

Prefix `cae6b1` and suffix `b7d32d` match exactly. **Same signing identity — continuity confirmed.**

## Other checks

- **Package/application ID unchanged**: `com.actionaura.clinic` / `com.actionaura.retail` — not
  touched this session, confirmed via `applicationId` in both `app/build.gradle` (unchanged from
  Phase 4 migration).
- **versionCode increased**: rc.1 → rc.2 both moved from a lower value to `versionCode 3` during
  Phase 7 (before this session); no further bump was needed or made this session since rc.2 was
  already the target version.
- **versionName changed to rc.2**: confirmed `"1.0.0-rc.2"` in both.
- **Signed upgrade would be accepted by Android**: Android's package installer only requires (a)
  matching `applicationId` and (b) matching signer certificate to accept an upgrade install over an
  existing one — both hold here. Actual on-device upgrade-install proof requires a physical device
  (Part J/K) and is marked NOT VERIFIED separately.
- **Distinct signing identities for Retail vs Clinic**: confirmed — two different keystores
  (`clinic-release.keystore` vs `retail-release.keystore`), two different certificate DNs (`OU=Aura
  Clinic` vs `OU=Aura Retail`), two different fingerprints. Matches established project policy.
- **No debug certificate in either release artifact**: both signed by the real production keystore
  DN (`CN=Action Aura, ...`), not the AOSP debug cert (`CN=Android Debug`).

## Verdict

**PASS** for both products — signer identity is unchanged from rc.1 to rc.2, confirmed by live
`apksigner` output against the actual rebuilt rc.2 APKs, not by re-stating the rc.1 record alone.
