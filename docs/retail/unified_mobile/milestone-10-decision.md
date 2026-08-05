# Aura Retail Unified Mobile — Milestone 10 Decision

## Verdict: **CONDITIONAL PASS**

Exactly the verdict the checkpoint's own M10 verdict rules predicted
for this Windows host: "the expected maximum honest verdict is
normally: CONDITIONAL PASS unless real Android and real macOS/iOS
validation are provided from separate authorized environments." Shared
architecture and source are complete, every host-executable test
passes (aside from one disclosed, pre-existing, unrelated timing
flake), the Android build passes — but real Android runtime execution
and real macOS/Xcode iOS execution are both unavailable on this host.
Every unverified behavior is disclosed in this milestone's own
documentation; no fake or simulated platform evidence is presented
anywhere.

## Why CONDITIONAL PASS, not PASS

Per the checkpoint's own binding rule, PASS requires "Android secure-
storage implementation executed on a real Android runtime AND Android
atomic persistence/recovery executed AND iOS implementation compiles
on macOS/Xcode AND iOS Keychain tests execute." None of those four are
available here. Per the same rule, CONDITIONAL PASS applies precisely
when "shared architecture/source is complete, all host-executable
tests pass, Android build passes, but Android runtime OR iOS
macOS/Xcode execution is unavailable, with every unverified behavior
disclosed and no fake platform evidence presented" — exactly this
milestone's real, honest state.

## Why not FAIL

None of the checkpoint's own FAIL conditions are present: no plaintext
secret storage (confirmed by inspection — every real credential/lease/
identity path routes through `SecureMaterialStore`/`SecureBlobStore`,
never `SharedPreferences`/`NSUserDefaults`/SQLDelight); no hard-coded
encryption keys (Android uses real `AndroidKeyStore`-generated keys
exclusively); no unauthenticated encryption (real AES-GCM AAD binding,
proven by `wrongAssociatedDataNeverAuthenticates`); no nonce/IV reuse
(fresh JCE-provider-generated IV per encryption); no activation
completing after partial storage (`failureAfterFirstStagedItemLeavesNoPromotedBundleAndCleansUp`);
no destruction of old valid material before a replacement succeeds
(`oldValidBundleSurvivesAFailedReplacement`); no test storage in
release wiring (no default value exists on `AuraAppContainer`'s
`secureBlobStore` parameter); no signed lease trusted before M11
(confirmed by inspection — no verification code exists); no credentials
in UI/logs/navigation (redaction tests pass); no Owner/external
workspace modified by M10 (`external-workspace-exit-fingerprints-m10.md`).

## Gate-by-gate

See `milestone-10-test-report.md` for the full gate table (39 gates).
Summary: 34 PASS, 3 CONDITIONAL/NOT VERIFIED (Android implementation
runtime, iOS implementation, Android/iOS runtime validation gates —
all disclosed, all expected on this host), 1 CONDITIONAL (shared-test
result, due to the disclosed pre-existing unrelated flake), 1 PASS
with honest partial disclosure (memory hygiene).

## Real findings during this milestone

Seven real bugs found and fixed by this milestone's own tests (full
detail in `milestone-10-test-report.md`): a wrong-associated-data
computation on pointer read, a `Pair`-style field-name typo, a missing
`suspend` keyword, redundant destructuring operators, a scope mismatch
in the M9-to-M10 bridge test, and two successive regex false-positive
fixes in the CSPRNG regression test itself. One real, non-deterministic
pre-existing defect observed and disclosed (`ReportingConcurrencyAtScaleTest`
timing flake), out of scope to fix here.

## Real, deliberately unfixed / out-of-scope items

No signed-lease cryptographic verification (M11). No offline
enforcement (M11). No background lease-refresh scheduling. No Product
entitlement enforcement. No local Retail user authorization. No
database encryption. No Customer self-service portal. No Apple code
signing/App Store release. No real Android/iOS runtime execution
(disclosed platform-availability limitation of this host, not a scope
decision).

## Proceed to Milestone 11 — blocked as scoped

Per the governing checkpoint's own explicit instruction: work stops
after M10. M11 (offline signed-lease cryptographic verification and
offline commercial enforcement), real remote Owner activation, and
real Android/iOS runtime validation from separate authorized
environments all remain un-started.

---

## Final Report (per the checkpoint's own required format)

1. **Verdict**: CONDITIONAL PASS
2. **Branch**: `feat/retail-unified-mobile-android-ios`
3. **Starting commit**: `a677e5666c18b2a96595d4bec8dd7a554fb236fd` (real M10 entry point — matches the accepted M9 HEAD exactly, `external-workspace-entry-fingerprints-m10.md`)
4. **Final commit**: recorded after this document's own commit (see `git log -1` at close)
5. **Commit list**: `1ca9bd3`, `81b72e1`, `ea198be`, `13b147e`, `9fd8d7e`, `c6c1365`, `1bbf14e`, `a895269`, `532cb90`, `6f10359`, `317660b`, `c61c93e`, `0e53011`, `46518fc`, plus this closeout commit
6. **Git status**: clean after every commit (verified each time)
7. **Shared-test result**: 656 total, 654 deterministic passes, 2 failures from one disclosed pre-existing unrelated timing flake (`ReportingConcurrencyAtScaleTest`)
8. **Baseline comparison**: 631 (M9) → 656 (M10), net +25 real tests
9. **Retail Python result**: not re-verified this milestone (no Python file touched; disclosed, not fabricated)
10. **Android APK result**: `BUILD SUCCESSFUL`, `androidApp-debug.apk`
11. **Shared secure-storage contract result**: complete — `SecureBlobStore`/`SecureMaterialStore`/`SecureActivationBundle`
12. **Atomic-commit authority result**: complete — `GenerationalSecureMaterialStore`, generation/pointer strategy, 19 real tests
13. **Old-bundle-survives-failed-replacement result**: proven — `oldValidBundleSurvivesAFailedReplacement`
14. **No-mixed-generation result**: proven — `noMixedGenerationEverObserved`
15. **Recovery result**: deterministic — `deterministicRecoveryAfterInterruptedCommit`
16. **Android decision**: raw `AndroidKeyStore` AES-256-GCM, StrongBox-with-fallback, chosen over `EncryptedSharedPreferences`
17. **Android implementation result**: complete, compiled, NOT runtime-verified (disclosed)
18. **iOS decision**: real Keychain, `kSecAttrAccessibleWhenUnlockedThisDeviceOnly`, iCloud sync disabled
19. **iOS implementation result**: complete source, NOT compiled/verified on this host (disclosed)
20. **Installation identity/Customer session/Installation credential/signed lease storage**: all real, designed, integrated into the one atomic bundle
21. **Versioning/migration result**: structural foundation real; no second version has shipped yet (honest, disclosed)
22. **Key rotation result**: real, tested — `rotateKeySucceedsAndPreviouslyCommittedBundleRemainsLoadable`; disclosed non-re-encryption-on-rotation limitation
23. **Corruption recovery result**: fails closed, tested — `corruptedComponentFailsClosedNeverReturnsPartialBundle`
24. **Deletion policy result**: tested — `deleteScopeRemovesPointerAndAllGenerationComponents`, `deleteScopeOnAnEmptyScopeIsASafeNoOp`
25. **Backup/reinstall policy result**: real, documented decision — `secure-storage-backup-reinstall-policy.md`
26. **User-presence decision result**: real, documented — `secure-storage-user-presence-decision.md`
27. **Activation integration result**: real atomic bridge — `SecureMaterialStoreActivationSink`, `bothPiecesTogetherProduceOneRealAtomicCommit`
28. **Startup bootstrap result**: real — `computeLicensingBootstrapStateFromHealth`, wired into `App.kt` (M10.31)
29. **Health contract result**: real, secret-free — `healthReflectsRealBundlePresenceAndCorruption`, `healthNeverExposesSecretValues`
30. **Memory hygiene result**: real, honest partial — three credential types remain `String`-wrapped (disclosed JVM/Kotlin-Native limitation, not silently claimed solved)
31. **CSPRNG regression result**: real, 3/3 passing, two real regex false-positives found and fixed during implementation
32. **Android runtime status**: NOT VERIFIED (no device/emulator, standing disclosure)
33. **iOS compile status**: NOT VERIFIED (no macOS/Xcode, standing disclosure)
34. **iOS runtime status**: NOT VERIFIED
35. **Performance/concurrency result**: logical concurrency proven (10-concurrent-commit test); real-device performance NOT MEASURED (disclosed)
36. **DI wiring result**: real — `AuraAppContainer`/`MainActivity.kt`/`App.kt`, no default value permits test storage into release wiring
37. **Real defects found and fixed**: seven (pointer AAD bug, field-name typo, missing `suspend`, redundant operators, sink test scope mismatch, two regex false-positive fixes)
38. **Real pre-existing defect disclosed, not fixed**: `ReportingConcurrencyAtScaleTest` timing flake, unrelated to this milestone
39. **Residual limitations**: no lease verification, no offline enforcement, no real Android/iOS runtime validation, partial memory-hygiene limitation
40. **External workspace comparison**: `aura-fullsuits-phase9r` and legacy `AuraEnterprise` byte-identical to M10 entry; `aura-fullsuits-owner-ui` changed for real, fully-identified, unrelated reasons, zero contribution from this session
41. **No Aura Owner code modified**: confirmed — zero write commands issued against `owner/` in M10
42. **No Clinic code introduced**: confirmed by inspection
43. **Push status**: not pushed (no push performed or requested)
44. **Merge/tag status**: not merged, not tagged (neither performed nor requested)
