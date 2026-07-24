# Phase 7V-F — Final Trust-Anchor Evidence (Part C/D)

## Generation

A real signing key was generated and activated against the production-like Owner instance
(`owner/app/licensing_service/signing.py`'s existing `generate_signing_key`/`activate_signing_key`
flow — the same Phase 6 mechanism, not a shortcut). Key id: `owner-ed25519-20260724T165808Z-03ef2f6e`.

Trust anchor generated via the existing `scripts/generate_trust_anchor.py` against this real Owner:

```json
{
  "keys": [
    {
      "key_id": "owner-ed25519-20260724T165808Z-03ef2f6e",
      "public_key": "vaWz570zElhgrUP5PBMxf/tP4xG7Gr/F5a7bgXusrQo=",
      "algorithm": "ed25519"
    }
  ]
}
```

Contains only public verification material (an Ed25519 public key, base64-encoded) — never a
secret. Bundled into all four final rc.2 artifacts this session (both Windows exes/installers via
each `.spec`'s conditional `datas` entry from Phase 7V, both Android APKs via Chaquopy's staged
Python source tree).

## Verification

- No trust-on-first-use: `OwnerTrustStore.bootstrap_from_anchor()` only ever admits keys present in
  this static file, never keys merely claimed active by a live `/signing-keys` response.
- Active signing-key ID included and matches what Owner's real `/service-info` endpoint reports.
- No unknown key trusted: every activation/check-in this session verified successfully against
  this exact key, proving the verification path (not just the bundling) works end-to-end.
- No retired/overlap key existed this session (single key generated, never rotated) — retired-key
  handling itself is unchanged Phase 6/7 code, not re-verified here.

## Rebuild discipline

When the trust anchor changed (a second licensing round required re-issuing licenses, but the
*signing key* itself was never rotated this session, so the trust anchor value stayed constant
throughout) — no rebuild was needed for that reason. Rebuilds that DID happen (multiple times) were
for the `cryptography` dependency fix and the trusted-time anchor-caching fix — every rebuild
re-verified the SAME trust anchor content was still correctly bundled (see
`final-artifact-reconfirmation.md`).

## Stale development trust anchor

None shipped — `commercial_runtime/licensing_contracts/trust_anchor.json` is gitignored and was
never committed; only ever present in the local working tree during active testing, matching the
exact discipline established in Phase 7V.
