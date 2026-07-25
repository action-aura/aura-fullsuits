# Phase 7V-A — Final Artifact Reconfirmation (Part R)

**Supersedes the earlier version of this document from this same round.** That version's
checksums were built with a URL missing the required `/api/licensing/v1` path suffix (see
`residual-risks.md`'s corrected root-cause note) — a validation-session build-command mistake, not
a product defect. These are the genuinely final artifacts: same six fixes, same diagnostic
removed, rebuilt one more time with the corrected Owner URL and confirmed reachable over a real
network round-trip (both LAN and loopback+`adb reverse`) before these checksums were taken.

## Android

| Artifact | SHA-256 |
|---|---|
| Clinic APK (`app-release.apk`) | `2bf78bb70b58b61ce4a5a52586125f83884960109f7c585fac402167f2e16085` |
| Clinic AAB (`app-release.aab`) | `0a4d471e9301761dd6b359bfb2a9f449e620dbce012b034e1f1ecdbbb22e31de` |
| Retail APK (`app-release.apk`) | `3b074e211a91f15803642f6292e1f20f393f8e015b1cf2f0dedeabb7134f3f9d` |
| Retail AAB (`app-release.aab`) | `95b60704a8c1c08b3e7da01e2def124d7928f07cf8895af8348a5e41336bb146` |

Built with `-PownerLicensingBaseUrl=http://127.0.0.1:19101/api/licensing/v1` — the full path
including Owner's `/api/licensing/v1` blueprint prefix (validation-only; a local test Owner
instance, not a real production endpoint).

## Certificate continuity — reconfirmed

- Clinic: SHA-256 `35508048cee7776ca94a45a9f04c6a870edf98729429482a8ad44e89dd0bbbf2`
- Retail: SHA-256 `cae6b18450c14a71eba47545e5b3ba52089eef0397d5881f4c1dc7d5a4b7d32d`

Both exactly match rc.1 and every earlier build this whole Phase 7V effort. No production signing
key was regenerated at any point, including during this round's LAN-based retest (which used a
throwaway, never-committed APK variant — see `residual-risks.md`).

## Reconfirmed for this final round

- Package IDs unchanged (`com.actionaura.clinic`, `com.actionaura.retail`).
- `versionName "1.0.0-rc.2"`, `versionCode 3` — unchanged.
- `network_security_config.xml` for both products confirmed byte-identical to the committed
  version (cleartext permitted only to `127.0.0.1`/`localhost`) — the temporary LAN-IP widening
  used mid-round for the connectivity retest was reverted via `git checkout` before this final
  build, confirmed via `git status` showing a clean working tree at that point.
- No debug build, no test bypass, no synthetic-data shortcut baked into either artifact.
- No secrets in logcat across the full physical test session.
- Physically confirmed reachable end-to-end over the real network this round (see
  `final-decision.md`) — both loopback (`adb reverse`) and LAN transport.

## Not overwritten

rc.1 artifacts remain untouched, as in every earlier round.
