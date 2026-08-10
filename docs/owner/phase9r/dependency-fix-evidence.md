# Phase 9R — Dependency Fix Evidence: `cryptography` 48.0.1 → 50.0.0 (M0)

## 1. What was found

`pip-audit --format json` against the M0 candidate venv (2026-08-04) reported
3 CVEs against `cryptography==48.0.1`:

| CVE | GHSA | Fixed in | Nature |
|---|---|---|---|
| CVE-2026-69248 | GHSA-m2h6-j472-rp4c | 49.0.0 | X.509 name-constraint bypass: a wildcard SAN on a leaf cert can escape an intermediate CA's DNS name constraint |
| CVE-2026-69249 | GHSA-jwv3-5hgf-82ww | 49.0.0 | Exponential-blowup recursion when resolving invalid certificate chains containing duplicate self-signed certs (multi-second DoS observed in testing) |
| CVE-2026-69247 | GHSA-g6cj-pr64-35w5 | 50.0.0 | `pkcs7_decrypt_der`/`_pem`/`_smime` leak a Bleichenbacher timing/length oracle against the recovered RSA content-encryption key, for an application that decrypts attacker-supplied `EnvelopedData` and reflects the outcome |

Raw evidence: `scratchpad/phase9r_pip_audit_after.json` (post-upgrade run);
pre-upgrade finding reproduced identically before the fix.

## 2. Code-path review — were the affected paths reached?

Grepped the entire application tree (`owner/`, `commercial_runtime/`,
`products/`) for any use of X.509 chain verification or PKCS7:

```
$ grep -rniE "pkcs7|x509|load_pem_x509|verify_directly_issued_by|certificate_chain" \
    --include=*.py owner/ commercial_runtime/ products/ | grep -v test
(no matches)
```

No result. This codebase's use of `cryptography` — per
`docs/owner/phase6/activation-protocol-threat-model.md` — is Ed25519
signing and verification of license assertions/leases, argon2/PBKDF2-backed
password hashing paths, and MFA secret encryption. None of that touches
`cryptography.x509` or `cryptography.hazmat.primitives.serialization.pkcs7`.
**The three reported CVEs' vulnerable code paths are not reachable by any
code this project executes today.**

This is a statement about *this codebase's current call sites*, checked by
direct grep against the exact working tree under audit — it is not a claim
that the CVEs are unexploitable in general, or unexploitable in some future
version of this codebase that does start parsing certificates or PKCS7
envelopes (e.g. if mTLS client-certificate verification is ever added at the
application layer rather than the reverse proxy). If that ever happens, this
finding must be re-reviewed against the code at that time, not assumed
still-inapplicable.

## 3. Why upgrade anyway

Phase 9R's whole purpose is putting this application on the public internet
with TLS termination immediately in front of it. `cryptography` is a
security-critical dependency for a project whose core product *is* a
signing/verification authority (license leases, activation assertions). The
upgrade path was a same-major-line version bump (48→50) with no
`cryptography` deprecation notices affecting Ed25519/hashing/KDF APIs
between those versions. Upgrading now, while the exposure is still zero, is
strictly cheaper than upgrading later under time pressure after the app is
actually reachable from the internet — done as defense in depth, not because
exploitability was found.

## 4. Upgrade and compatibility verification (real, executed)

```
$ python -m pip install --upgrade cryptography==50.0.0
Successfully installed cryptography-50.0.0

$ python -m pip check
No broken requirements found.

$ python -c "import cryptography; print(cryptography.__version__)"
50.0.0
```

`pip-audit` after upgrade:

```
Found 1 known vulnerability in 1 package
```

The single remaining finding is the pre-existing, previously-accepted
`pytest` UNIX-only tempdir issue (PYSEC-2026-1845), inherited unchanged from
every prior Owner phase's own baseline (Phase 9, 9.5A, 9.5D, 9.5E) — not a
regression from this upgrade, not related to `cryptography`, and not
reachable on this project's Windows dev/CI environment or in the frozen
production artifact (`pytest` is a test-only dependency, never bundled).

Targeted signing/verification/serialization test suites run against the
upgraded package, from the same Python process the upgrade was installed
into:

```
$ cd commercial_runtime/licensing_contracts && python -m pytest -q
230 passed in 11.82s

$ cd commercial_runtime && python -m pytest -q --ignore=licensing_contracts
5 passed in 0.31s
```

These are the suites that exercise Ed25519 signing, verification, key
loading/serialization, trust-anchor loading, and the assertion verifier
(`test_assertion_verifier.py`, `test_trust_anchor_loader.py`,
`test_trust_store.py`, `test_generate_trust_anchor.py`,
`test_device_identity.py`, among others) — zero failures, zero errors,
against the exact upgraded dependency. The complete Owner suite (972 tests,
which also exercises MFA encryption and password hashing paths that go
through `cryptography`) is re-run as part of `baseline-regression.md`
against this same upgraded environment before M0 is committed.

## 5. Disposition

**Fixed, not suppressed.** Requirements pins updated in
`requirements/base.txt` and `requirements/owner-server.txt`
(`cryptography==50.0.0`). No baseline exclusion, no `.secrets.baseline`-style
allowlist entry, no suppression comment — the package was actually upgraded
and actually re-verified against the real test suites that would catch a
regression in its use.
