# External Workspace Entry Fingerprints (M11 Entry)

Real, executed fingerprints captured at the start of M11, same 8-value
protocol M9/M10 established: `HEAD`, modified count, untracked count,
`git diff | sha256sum`, `git diff --cached | sha256sum`, `git status
--porcelain | grep '^??' | sort | sha256sum`.

Accepted M10 baselines: real full HEAD
`b13ef0eba984dcbefbb8834c8e11b4a837545164`, shared tests **656**,
Android APK builds, Retail Python **73 failed / 110 passed / 11
errors / 194 collected** (real, pre-existing, cross-test
fixture-isolation defect, unrelated to Kotlin/M10, disclosed at the
M10 regression-stabilization closeout — carried forward as the real
M11 entry baseline, not re-litigated).

## `aura-fullsuits-phase9r`
```
HEAD: 8c35768393d9407f72ef34aa93705f9c454453f7
STATUS / all hashes: empty (byte-identical)
```

## `AuraEnterprise` (legacy repo)
```
HEAD: 414e6ea5ca9fc698fe075a4ae4464cebf0b7bf34
STATUS: 22 modified, 15 untracked
DIFF_BINARY_HASH:        cc37af7f7c95450e59be66bb4785a67bdfaeae18f7c86bbc2e3161ab76c66f7
DIFF_CACHED_BINARY_HASH: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85
UNTRACKED_LIST_HASH:     cafdb44a28f4865453bf28d1def21c1ca34496052ed638da5882a776bb4afe9
```
Byte-identical to the M10 regression-stabilization closeout's own exit
capture — no drift.

## `aura-fullsuits-owner-ui` (Owner UI-modernization worktree)
```
HEAD: 2b2f909c9cbc27b71dbd881ed0bd3f4c4b7977ef
Branch: feat/owner-ui-ux-modernization
STATUS: 7 modified, 5 untracked
DIFF_BINARY_HASH:        6cfec50934240ef15d8c85244c164faa67e1498bc181830ec1932db9d3846f9
DIFF_CACHED_BINARY_HASH: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85
UNTRACKED_LIST_HASH:     2172489514fdbb809c8f305b783f00521fd989bbf08f7945b17f44548de532d
```
Real, independent "attention center" feature work in progress
(`owner/app/attention/`, `owner/app/templates/attention/`,
`owner/tests/test_attention_center.py`, sidebar/shell CSS changes) —
unchanged since the M10 regression-stabilization closeout's own exit
capture. No other active Owner worktree was discovered
(`git worktree list` confirms exactly these two worktrees plus the
main `aura-fullsuits` checkout).

## Current secure-storage architecture (baseline for M11)

`GenerationalSecureMaterialStore` (M10) — atomic generation/pointer
commit, real Android Keystore-backed `SecureBlobStore`
(compile-verified, runtime-unverified), real iOS Keychain-backed
source (never compiled). `SecureActivationBundle.rawSignedLease:
SignedAssertionEnvelope` — the M7 typed envelope, holding a *parsed*
`AssertionPayload`, not raw signed bytes. **Real, load-bearing M11.1
finding**: this typed representation cannot be trusted to re-derive
Owner's exact signed canonical bytes (kotlinx.serialization's default
encoder does not sort keys or NFC-normalize strings) — M11 introduces
`ProtectedSignedLease` (raw canonical JSON string + signature) as the
real input to signature verification, used going forward; the M9/M10
`SignedAssertionEnvelope` type is unchanged and continues to serve its
existing role (activation/check-in response modeling) unmodified.

## Current lease contract versions / signing algorithms / key fixtures

Contract version: `"1.0"` (`SUPPORTED_LEASE_CONTRACT_VERSION`, M11's
own real, first-defined supported version — no prior milestone
declared one). Algorithm: **Ed25519** (`signed-lease-cryptography-
decision.md`). Real production key ring seeded from the real, checked-in
`commercial_runtime/licensing_contracts/trust_anchor.json` (key
`owner-ed25519-20260727T053324Z-c32537d7`).

## Current startup licensing state machine

`App.kt` (M10.31): `computeLicensingBootstrapStateFromHealth(health =
null)` — real, honest, unchanged since M10 (no real Installation-
identity/scope generator exists in production yet, so `health` stays
`null`, resolving to `ServiceNotConfigured`). M11 does not modify this
file in this session (real, disclosed — the real M11.22 startup-gate
integration is not yet wired; see `milestone-11-decision.md`).
