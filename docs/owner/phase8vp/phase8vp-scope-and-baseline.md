# Phase 8V-P — Scope and Baseline

## Conditional tag and commit history since

- `aura-owner-commercial-ops-phase8-conditional-complete` -> `7150564af95bd75cd475df57a6b42ae9ad4b3fb5`
  (unchanged, unmoved, verified again this session).
- Starting HEAD this session: `709ccf1` (`docs: complete phase 8v closure documentation`), branch
  `master`, working tree clean at session start.
- Commits since the conditional tag (all from Phase 8V, the immediately preceding phase):
  `b1e1ae8` (UI closure planning docs), `e965121` (full Owner UI backlog), `4213666` (19 new UI
  route tests), `ff2a3c7` (real cross-package wire-level scenario harness + the `ALLOWED_PAYLOAD_FIELDS`
  P1 fix), `709ccf1` (Phase 8V closure documentation).

## User decision governing this session's scope

Real `adb devices -l` (via the full platform-tools path,
`C:\Users\Dell\AppData\Local\Android\Sdk\platform-tools\adb.exe` — not on `PATH` in this shell but
present on disk) returned an empty device list — confirmed, not assumed from a prior session. Per
this phase's own Part B instruction, mandatory Android physical validation stopped there and the
user was asked how to proceed. Chosen: **do every non-Android-dependent part of this phase now**
(baseline reconfirmation, version decision, Windows Clinic/Retail frozen builds + installers,
migration reconfirmation, full Owner/commercial_runtime/product-backend regression, real traffic
capture via the actual installed Windows products), stop short of physical Android scenarios and the
final tag, and report the exact remaining gate honestly. See `physical-device-readiness.md`.

## Environment versions recorded

- PostgreSQL: `17.10`
- Java: `openjdk 17.0.19` (Microsoft build)
- Python (shared `.venv`): matches prior phases, unchanged
- Android SDK present on disk (`C:\Users\Dell\AppData\Local\Android\Sdk`), platform-tools/`adb.exe`
  present and functional; no device attached to it.
- `gradle` (standalone) not on `PATH` — irrelevant this session since no Android build is attempted;
  every prior Android build in this project used the per-project `gradlew` wrapper, which remains
  available and untouched.

## What this document set does NOT claim

Nothing in `docs/owner/phase8vp/` this session claims physical Android evidence. Every scenario
document is explicit about its Windows-only scope. See `remaining-physical-gate-matrix.md` for the
itemized gap this leaves, and `phase8-final-unconditional-decision.md` for why the final tag is
still withheld.
