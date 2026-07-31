# Phase 8V-P7 — Final Artifact Dependency Analysis

## Android (Clinic + Retail) — embedded copy, confirmed in Phase 8V-P6

`android/{aura-clinic,aura-retail}/app/build.gradle`'s `stagedPythonSources` Gradle task physically
copies `aura-fullsuits/commercial_runtime` into `app/build/staged-python/commercial_runtime` before
Chaquopy packages it into the APK/AAB. **Yes, embedded. Yes, copied at build time. No, not imported live
at runtime.** An already-built APK/AAB from before the fix commit (`45fe6b8`) contains the stale
evaluator and cannot exhibit the new behavior. Both products' currently-installed/previously-built
artifacts predate the fix -- **both need rebuilding** for physical validation to be valid.

## Windows (Clinic + Retail) — also an embedded copy, NOT live-imported (new finding this session)

`products/{retail,clinic}/packaging/aura_{retail,clinic}.spec` (PyInstaller specs) build a frozen
executable via `pyinstaller ... --noconfirm` from `products/{retail,clinic}/desktop/launcher_*.py` as
entry point, with `pathex=[ROOT, BACKEND]` (`ROOT` = repo root, which contains `commercial_runtime` as
a top-level package). PyInstaller's static import analysis discovers
`commercial_runtime.licensing_contracts.*` transitively (via `products/*/backend/app.py`'s
`from commercial_runtime.licensing_contracts.routes import make_licensing_blueprint`, confirmed present
in both backends) even though `licensing_contracts` is **not** explicitly listed in either spec's
`hiddenimports` (only `commercial_runtime.identity.*`/`security.*`/`backup.*`/`launcher_support` are
listed explicitly -- these are the modules PyInstaller's static analyzer cannot reach on its own,
typically because they're imported dynamically or conditionally; `licensing_contracts` is reached
via ordinary top-level imports and gets swept in automatically). The frozen executable is a real,
physical **snapshot** -- source changes on disk after a build has no effect on that build's `.exe`.

**Confirmed, directly, by file timestamp**: `dist/AuraRetail/AuraRetail.exe` and
`dist/AuraClinic/AuraClinic.exe` were both built `2026-07-27 08:39-08:40` -- four real days **before**
the fix commit (`45fe6b8`, `2026-07-31 08:49:35`). Both existing Windows executables contain the stale,
pre-fix `policy_evaluator.py`. **Both need rebuilding** before either can be used for any physical
validation involving the corrected commercial-enforcement logic. This corrects Phase 8V-P6's own
`build-impact-decision.md`, which concluded "Windows rebuild not required" -- that conclusion was
accurate for that session (no Windows physical scenario was attempted, so the question of Windows
staleness never became load-bearing), not a general claim that Windows is unaffected.

## Per-product answers

| Question | Clinic Android | Retail Android | Clinic Windows | Retail Windows |
|---|---|---|---|---|
| Embedded in artifact? | Yes | Yes | Yes | Yes |
| Copied at build time? | Yes (`stagedPythonSources`) | Yes (same) | Yes (PyInstaller freeze) | Yes (same) |
| Imported live at runtime? | No | No | No | No |
| Can an existing artifact contain the pre-fix evaluator? | Yes -- and did, until Phase 8V-P6's rebuild | Yes -- current build predates the fix | Yes -- current build predates the fix | Yes -- current build predates the fix |
| Needs rebuilding this session? | Only if version/artifact alignment requires re-cutting rc.4 (source already correct since Phase 8V-P6) | **Yes** | Yes, for artifact-matrix completeness (no physical Clinic-Windows scenario planned this session) | **Yes** (needed for Scenario 6/7 identity B) |
| Installer needs rebuilding? | N/A (APK/AAB is the installer) | N/A | Yes, if Inno Setup/canonical installer wraps the exe | Yes, same |
| Version needs incrementing? | Yes, per `version-alignment-decision.md` | Yes | Yes | Yes |
| Proof of correct evaluator embedded | Phase 8V-P6 physical Scenario 3/5 evidence (real device); this session re-confirms via smoke check | This session's own Scenario 2 physical evidence | Not physically exercised this session (see disposition below) | This session's own Scenario 6/7 evidence, to the extent the single-machine constraint allows |

## Installer tooling

No Inno Setup `.iss` script was found referencing these two products in this repo pass (a targeted
search would be needed to confirm one way or the other with full certainty; not exhaustively ruled out,
but the existing `dist/AuraRetail/`, `dist/AuraClinic/` directories are themselves the PyInstaller
`COLLECT` onedir output -- i.e., what would previously have been evidenced as "the installable product"
in prior sessions' own artifact reports). This session treats the PyInstaller onedir rebuild itself as
the required artifact; a separate installer-wrapping step, if the project has one, is out of this
session's demonstrated scope unless found during the build step.
