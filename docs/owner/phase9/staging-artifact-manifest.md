# Phase 9 Milestone 13 — Staging Artifact Manifest

## Status: NOT APPLICABLE this session — no rc.6 artifacts exist

See `staging-product-build-report.md` for why building them now would be premature/dishonest (no real
HTTPS staging URL to embed).

## Template (to be filled in when a real build occurs)

| Field | Value |
|---|---|
| Product | |
| Platform | |
| Version | `1.0.0-rc.6` (expected) |
| versionCode | `7` (expected, monotonic from current `6`) |
| Source commit | |
| Filename | |
| SHA-256 | |
| Size | |
| Signing fingerprint/status | must match the existing rc.5 fingerprint (continuity, `cae6b18450...` Retail / `35508048...` Clinic) |
| Package/application ID | must match rc.5 exactly (`com.actionaura.retail` / `com.actionaura.clinic`) |
| Embedded staging URL | `https://<real staging domain>/api/licensing/v1` |
| Embedded runtime hash | commercial_runtime commit/hash embedded in this build |
| Trust-anchor version | must reference the real staging signing key generated in Milestone 6 |
| Assertion-contract version | `v1` (unchanged) |
| Build timestamp | |
| Validation status | |

## Current, still-valid rc.5 manifest (unaffected reference point)

See `docs/owner/phase8vp9/final-artifact-and-manifest-report.md`.
