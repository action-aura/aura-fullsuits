# Phase 9 Milestone 13 — Staging Product Build Report

## Status: NOT BUILT this session — precondition explicitly not met

The governing instruction is explicit: *"Create new immutable staging/pilot artifacts only after the
final HTTPS staging URL is active and verified."* No real staging URL exists this session (Milestone
12 — no remote host, no domain, no public TLS). Building rc.6 artifacts now would mean either (a)
embedding a fake/placeholder URL, which the instruction separately forbids ("Do not use... localhost...
development certificates not trusted by the established model"), or (b) building artifacts that would
need to be rebuilt again the moment a real URL exists — wasted, dishonest work either way. Correctly
not attempted.

## What is real and unaffected

The current rc.5 artifacts (Phase 8V-P9, `1.0.0-rc.5`, `versionCode 6`) remain the current, valid,
tagged artifact family — untouched, not superseded, not invalidated by anything in this phase. See
`docs/owner/phase8vp9/final-artifact-and-manifest-report.md` for their real checksums/signing
fingerprints, unchanged.

## What Phase 9 changed that would matter for the next real build

- `requirements/base.txt` (shared by Retail/Clinic): `cryptography` bumped `43.0.1 -> 48.0.1`,
  `flask`/`werkzeug`/`flask-cors`/`waitress`/`requests` bumped to their fixed versions
  (`dependency-risk-register.md`). Any future Retail/Clinic Windows build (PyInstaller) will pick these
  up automatically from `requirements/base.txt` at build time. Android does not use this file (Chaquopy
  has its own separate pinned pip list per `android/aura-*/app/build.gradle` — unaffected by this
  session's change, would need its own explicit review before rc.6).
- No `commercial_runtime`/`licensing_contracts` business logic changed this phase — the stale-assertion
  guard and every other Phase 8V-P9 fix are unchanged.

## Expected next version (per this project's own versioning policy, when the precondition IS met)

`1.0.0-rc.6`, Android `versionCode 7` (monotonic increase from the current `6`) — confirmed by reading
`android/aura-retail/app/build.gradle`/`android/aura-clinic/app/build.gradle` this session
(`phase9-baseline.md`), not assumed.
