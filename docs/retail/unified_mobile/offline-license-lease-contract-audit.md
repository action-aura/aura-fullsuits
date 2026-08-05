# Offline License Lease Contract Audit (M7.13)

Real canonical lease claim set, algorithm, key rotation, and
verification behavior in `commercial_runtime/licensing_contracts/` —
full detail already established in `installation-credential-contract.md`
(credential type 1) and cross-referenced here rather than repeated in
full; this document adds the audit's own conclusions plus the real,
disclosed fixture gap and its M7.18 remediation.

## Real canonical claim set (summary, full list in installation-credential-contract.md)

`assertion_id, issuer, product_code, license_public_id,
installation_public_id, platform, app_version_policy, release_channel,
issued_at, not_before, expires_at, license_status, installation_status,
subscription_status, allowed_device_count, device_key_fingerprint,
entitlements, offline_policy, contract_version,
commercial_policy_version, renewal_status, plan_code, term_start,
term_end, past_due_since, commercial_grace_end, pilot_status,
emergency_extension_id` — `ALLOWED_PAYLOAD_FIELDS`,
`assertion_verifier.py:59-100`, mirrored against a
`FORBIDDEN_ASSERTION_MARKERS` guard (lines 38-57) on both the server
and client sides.

## Real algorithm

Ed25519, both for the device's own request-signing key and Owner's
distinct assertion-signing `SigningKey` — same primitive, separate key
material, confirmed in `installation-credential-contract.md`.

## Real key rotation

Confirmed multi-key, versioned verification: `OwnerTrustStore` keeps a
`dict[key_id, TrustedKey]` (`trust_store.py:45`), each with
`status: ACTIVE|RETIRED`; `admit_manifest()` only accepts a rotation
manifest signed by an already-trusted key (`trust_store.py:76-121`);
a RETIRED key remains verifiable for assertions it already signed;
only explicit REVOKED status permanently invalidates a key, even
retroactively. Bundled first-run anchor:
`commercial_runtime/licensing_contracts/trust_anchor.json` (real file,
one entry, `key_id: "owner-ed25519-20260727T053324Z-c32537d7"`).

## Real offline verification behavior

`verify_assertion()` (`assertion_verifier.py:130-247`) is pure,
local, offline — no network call. Rejects: unknown algorithm, untrusted
signing key, tampered payload (canonical-signature mismatch), expired
(`expires_at` vs. caller-supplied `trusted_now`, 60s clock-skew
tolerance), not-yet-valid (`not_before`), product/platform/
installation/device-fingerprint mismatch, and any forbidden payload
field. Composed with `OfflinePolicyEvaluator.evaluate()`
(`policy_evaluator.py:70-173`) for grace/hard-expiry/clock-rollback
behavior, and `TrustedTime` (`trusted_time.py`) for rollback detection
— confirmed pure/local via `test_checkin_scheduler.py`'s own
`test_network_failure_falls_back_to_offline_evaluation_not_immediate_
restriction`.

## Real, disclosed gap found during this audit: the referenced shared fixture file does not exist

`canonical.py`'s own module docstring (lines 8-11) states: "covered by
the shared conformance fixtures in `commercial_runtime/
licensing_contracts/tests/fixtures/canonical_vectors.json`, which both
this module's tests and Owner's own test suite can be checked against
to catch drift." **A real, direct file-existence check during this
audit confirms no such file exists anywhere under
`commercial_runtime/licensing_contracts/tests/`** (no `fixtures/`
directory, no `.json` file of any kind in that tree). This is a real,
previously-undocumented drift-risk gap — the cross-checking mechanism
the docstring promises is aspirational, not implemented. Classified in
`licensing-gap-ownership-matrix.md` as `COMMERCIAL_RUNTIME` (owned by
that package's own maintainers, not fixable by mobile client code) —
M7 does not modify `commercial_runtime/`, only documents the finding
and works around it for the mobile-side fixture suite below.

## M7.18 sanitized fixture suite (mobile-side, workaround for the gap above)

Because no reusable, versioned sanitized fixture set exists upstream,
M7.18 builds the required fixture set independently, mobile-side,
under `shared/src/commonTest/.../licensing/fixtures/`, generated with
a **locally-generated, disposable Ed25519 test keypair** (never a real
Owner production key, never committed private-key material beyond the
throwaway test key itself, generated once for fixture construction and
disclosed as such). Required scenarios (all present, see
`licensing-contract-fixture-report.md` for the executed inventory):
valid, grace, expired, suspended, revoked, wrong-product,
wrong-platform, unknown-key, malformed-signature, future-version,
min-version-failure (documented as `NOT_APPLICABLE_WITH_EVIDENCE` per
`mobile-release-version-contract.md`'s finding that no min-app-version
enforcement exists server-side — the fixture instead exercises the
real `VERSION_UNSUPPORTED`/`VERSION_NOT_ALLOWED` public reason codes
that do exist, applied at the contract-version level, not an app
semantic-version level).

## Real test coverage (existing, `commercial_runtime` package)

`test_assertion_verifier.py`, `test_trust_store.py`,
`test_checkin_scheduler.py`, `test_canonical.py` — 4 of the 24 real
test files most directly exercising this contract; full 24-file list
in `licensing-authority-source-map.md` tier 4.
