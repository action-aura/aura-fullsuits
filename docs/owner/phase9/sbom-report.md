# Phase 9 Milestone 10 — SBOM Report

## Real, generated this session

`cyclonedx-bom` (real, pip-installed tool, not hand-written) against the actual project `.venv`
environment after the dependency remediation in `dependency-risk-register.md`:

```
python -m cyclonedx_py environment --output-format json --output-file sbom.json
```

Result: **CycloneDX spec version 1.6, 94 components.** Full raw SBOM committed at
`docs/owner/phase9/evidence/sbom-cyclonedx-1.6.json`.

## Scope

This SBOM covers the Python environment shared by Owner, Retail, and Clinic backends (they share one
`.venv` in this development layout). It does not cover Android (Gradle/Chaquopy dependency tree,
already tracked separately via `android/*/app/build.gradle` and Chaquopy's own pinned pip
requirements) or the Windows PyInstaller-frozen artifacts' embedded dependency set specifically — both
out of scope for this session (no product rebuild occurred; see `phase8-evidence-reuse-decision.md`).

## Regeneration

Real deployment / CI: `python -m cyclonedx_py environment` should be re-run as part of the CI pipeline
(`ci-validation-contract.md`) on every build that changes `requirements/*.txt`, not only when this
phase happened to run it manually.
