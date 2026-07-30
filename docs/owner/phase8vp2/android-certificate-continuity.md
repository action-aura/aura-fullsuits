# Phase 8V-P2 — Android Certificate Continuity

Verified with `apksigner verify --print-certs` (Android SDK build-tools 34.0.0), reading the full
SHA-256 digest, not trusting the abbreviated prefix/suffix alone.

## Clinic

```
$ apksigner verify --print-certs android/aura-clinic/app/build/outputs/apk/release/app-release.apk
Signer #1 certificate DN: CN=Action Aura, OU=Aura Clinic, O=Action Aura, L=Amman, ST=Amman, C=JO
Signer #1 certificate SHA-256 digest: 35508048cee7776ca94a45a9f04c6a870edf98729429482a8ad44e89dd0bbbf2
```

Matches the historical production signing identity in full (prefix `35508048` / suffix `0bbbf2`
confirmed as the same certificate, not merely a matching prefix/suffix coincidence -- the entire
64-character digest was read and compared).

## Retail

```
$ apksigner verify --print-certs android/aura-retail/app/build/outputs/apk/release/app-release.apk
Signer #1 certificate DN: CN=Action Aura, OU=Aura Retail, O=Action Aura, L=Amman, ST=Amman, C=JO
Signer #1 certificate SHA-256 digest: cae6b18450c14a71eba47545e5b3ba52089eef0397d5881f4c1dc7d5a4b7d32d
```

Matches the historical production signing identity in full.

## AAB signing (bundle)

`jarsigner -verify` against both `.aab` files: `jar verified.` for both (self-signed certificate
warning expected and correct -- this is a real, non-CA-chained release keystore, the same one used
for every prior rc build; no timestamp-authority warning is a known, accepted, pre-existing condition
carried forward unchanged from Wave 1B/Phase 7V-A, not something this session introduced).

## Debug certificate check

Neither APK's signer DN is the well-known AOSP debug certificate (`CN=Android Debug`) -- both are the
real `Action Aura` production identity. Combined with the `assembleRelease`/`bundleRelease` task
names actually used (never `assembleDebug`), this confirms the debug keystore was not substituted.

## Conclusion

**PASS for both products.** Package IDs unchanged (`com.actionaura.clinic`,
`com.actionaura.retail`), signing identity unchanged, upgrade continuity from any prior signed
rc.1/rc.2 install is preserved. Safe to install as an in-place upgrade over any existing signed
installation once a physical device is available.
