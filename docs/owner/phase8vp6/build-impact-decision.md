# Phase 8V-P6 — Build Impact Decision

## Decision: Android rebuild REQUIRED (both products). Windows rebuild not required for this
session's physical validation (no physical Windows scenario exercised this session; documented for
completeness).

## Why

`android/aura-clinic/app/build.gradle` (and the Retail equivalent) has a `stagedPythonSources` task
that copies `aura-fullsuits/commercial_runtime` into `app/build/staged-python/commercial_runtime`
before Chaquopy packages it into the APK. This is a real, physical **copy**, not a live reference --
the APK currently installed on the physical device (rc.3, from Phase 8V-P4) was built before this
session's `policy_evaluator.py`/`assertion_verifier.py` changes existed, and therefore **cannot**
exhibit the new subscription-status/license-SUSPENDED/emergency-extension-wiring behavior no matter
what Owner sends it. Any physical scenario claiming to validate the new logic against the
currently-installed APK would be validating stale code, not the fix -- this would be a false
positive, explicitly disallowed by this session's own governing spec ("do not report zero P1 without
reassessing" / general honesty requirements carried from every prior session).

## What must be rebuilt

Both `android/aura-clinic` and `android/aura-retail` (both stage the same shared `commercial_runtime`
directory) -- since the new logic is entirely inside `commercial_runtime/licensing_contracts`, neither
product's own Kotlin or product-specific Python code needs to change, only the staged copy needs to be
current.

## Version/signing policy

Same versionName/versionCode (`1.0.0-rc.3` / `4`) -- this is a same-source-of-truth fix rebuild, not a
new release; the governing spec's own Part L allows using the existing production signing keys and
does not require a version bump for a validation-cycle rebuild. Package IDs and signing keys are
unchanged (confirmed after build, see `final-build-and-signing-report.md`).

## Owner URL

Unchanged: `http://127.0.0.1:5551/api/licensing/v1`, injected the same way (`-PownerLicensingBaseUrl`
gradle property), since the validation Owner instance's address has not changed this session.
