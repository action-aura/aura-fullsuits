# Phase 8V-P7 — Final Artifact Alignment Plan (as executed)

1. Determine dependency: `commercial_runtime` is a real, physical, build-time copy in all four
   artifact families (Android via `stagedPythonSources`, Windows via PyInstaller freeze) -- confirmed,
   not assumed (`artifact-dependency-analysis.md`).
2. Confirm staleness: both `dist/` Windows executables predated the fix by 4 real days (file timestamps
   vs. commit timestamp) -- real, direct evidence, not inference.
3. Bump version across all 8 canonical sources per the project's own documented policy
   (`version-alignment-decision.md`).
4. Rebuild all four artifact families from final HEAD (`final-build-report.md`).
5. Verify signing/package/certificate continuity on every rebuilt artifact
   (`signing-and-artifact-continuity.md`).
6. Install/upgrade in place on the physical device (Android) and launch fresh (Windows), confirming
   identity continuity throughout.
7. Use the rebuilt artifacts, not the stale ones, for every subsequent physical scenario this session.
