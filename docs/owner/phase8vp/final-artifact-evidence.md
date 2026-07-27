# Phase 8V-P — Final Artifact Evidence (Part V)

Windows artifacts: see `final-artifact-build-report.md` for the full table (product, platform,
version, filename, SHA-256, size). Android: not built this session (see
`physical-device-readiness.md`).

## Inspection results

| Check | Result |
|---|---|
| Secrets/private keys in artifacts | None found (build sources config from environment, never bakes a key in) |
| Full license keys | None found (structurally never present in source) |
| Synthetic data only | Yes — all this session's test data explicitly labeled "Phase 8V-P Synthetic ... Co" |
| Developer endpoints | None hardcoded — `AURA_OWNER_LICENSING_URL` is runtime env config |
| `/api/licensing/v1` suffix present | Yes, verified live via real `/service-info`/`/signing-keys` calls before any scenario work (see `environment-readiness-report.md`) |
| HTTP production endpoint | No production endpoint configured anywhere — this session used local loopback only |
| TLS bypass in artifact | No — `OWNER_LICENSING_VERIFY_TLS` hardcoded `True` for any frozen build, unconditionally |
| Hidden free mode / fake paid state | None found — every real activation this session went through the real, unmodified activation protocol |
| Debug mode | PyInstaller specs' own existing `debug=False`, unchanged |
| Fake clock | None found or added |
| Customer business/medical data | None — see `product-data-preservation-report.md` |
| Phase 9 deployment config | None added |

## Release manifest

No pre-existing generated-manifest file (`release-candidate-manifest.md`-style) was found under
`docs/owner/phase8*/` to update additively; the version/checksum table above and
`final-product-version-decision.md` together serve that role for this release, matching this
project's `docs/release/versioning-policy.md`'s own "every release artifact must expose" checklist.

## Previous artifact history: untouched

`dist/installers/AuraClinic-Setup-1.0.0-rc.1.exe`, `-rc.2.exe`, `dist/installers/AuraRetail-Setup-1.0.0-rc.1.exe`,
`-rc.2.exe` all still present, unmodified, alongside the new rc.3 artifacts.
