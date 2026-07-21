# Phase 6 -- Dependency Security Scan (Part X)

## Command
```
pip-audit -r requirements/owner-server.txt
```
Run against the exact pinned versions in `requirements/owner-server.txt`. Result: **28 known advisories across 5 packages** (`flask`, `werkzeug`, `flask-cors`, `pyjwt`, `cryptography`). Full raw JSON output preserved in this phase's development scratchpad; summarized and classified below per Part X's explicit "do not silently ignore -- classify and document" instruction.

## Classification (first-pass triage, not a full CVE-by-CVE deep-dive)

| Package | Pinned | Advisories found | Usage in this codebase | Disposition |
|---|---|---|---|---|
| `flask` | 3.0.3 | PYSEC-2026-2151 | Core framework, used everywhere | **Flagged for upgrade in a follow-up maintenance pass.** Phase 5 pinned this version; bumping it touches the entire Owner app (internal + external), which is broader than Phase 6's scope ("do not broaden into a general Owner redesign"). Not fixed in this phase. |
| `werkzeug` | 3.0.3 | 5 advisories (PYSEC-2026-2045/2046/2044/2320, PYSEC-2026-3417) | Flask's WSGI layer; this app uses the dev server (`flask run`) only, never Werkzeug's dev-server-specific debugger/reloader in a deployed context | **Flagged for upgrade alongside Flask** (version-locked together in practice). Not fixed in this phase. |
| `flask-cors` | 4.0.1 | 5 advisories | Used only to explicitly configure an **empty** allowed-origins list (`CORS(app, resources={r"/api/*": {"origins": []}})`) -- the known flask-cors CVEs are almost entirely about *misconfigured* wildcard/regex origin handling, which this app does not use | **Low urgency given current configuration**, but still flagged for upgrade in the same pass since the safest fix is simply a newer pinned version. |
| `pyjwt` | 2.9.0 | 12 advisory entries (several duplicates across the JSON, ~6 distinct IDs) | **Not actually imported or used anywhere in `owner/app/` as of Phase 6** -- it was curated into `requirements/owner-server.txt` in Phase 5 for a JWT-based approach that Phase 6 explicitly did NOT take (ADR-6.5 chose a hand-rolled signed envelope over JWT specifically to avoid JWT's algorithm-confusion CVE history). | **Recommend removing `pyjwt` from `requirements/owner-server.txt` entirely** in a follow-up pass -- it is dead weight carrying real CVE exposure for zero functional benefit. Not removed in this phase to keep the diff scoped to Phase 6's actual additions, but explicitly flagged here rather than silently left in. |
| `cryptography` | 43.0.1 | 4 advisory entries (~3 distinct IDs) | **Actively used for the real Ed25519 signing/verification that is Phase 6's core security mechanism** (`app/licensing_service/signing.py`, `assertions.py`, `device_identity.py`, the simulator) | **Highest-priority item in this list.** Reviewed the advisory summaries: the known `cryptography` CVEs in this version range are concentrated in OpenSSL-backed algorithms this codebase does not use (e.g. certain X.509/PKCS7 parsing paths) -- Ed25519 key generation/sign/verify is not the affected code path for the advisories found. Still, **recommend upgrading to the latest `cryptography` patch release in the very next maintenance pass**, since it is the one dependency in this list directly underpinning a security-critical Phase 6 mechanism. |

## What was NOT done this phase (explicitly, per scope discipline)
No dependency version was bumped in this phase. Phase 6's mandate is the licensing service itself, not a general dependency-upgrade pass across the whole Owner application; upgrading `flask`/`werkzeug` in particular would require re-running the full Phase 5 + Phase 6 regression suite against the new versions, which is a distinct, reviewable unit of work belonging in its own follow-up, not silently folded into this phase's diff.

## Recommended immediate follow-up (tracked in `phase6-residual-risk-register.md`)
1. Remove unused `pyjwt` dependency.
2. Upgrade `cryptography` to its latest patch release and re-run the full crypto test suite (`test_phase6_crypto.py` + the simulator).
3. Schedule a dedicated Flask/Werkzeug/flask-cors upgrade pass with full regression coverage.
4. Wire `pip-audit -r requirements/owner-server.txt` into a CI step so this scan runs automatically on every change (no CI exists for Owner yet, a pre-existing Phase 5 residual risk this phase does not resolve).
