# External Workspace Entry Fingerprints — Owner UI Modernization

Captured immediately after creating branch `feat/owner-ui-ux-modernization`
from authoritative commit `8c35768393d9407f72ef34aa93705f9c454453f7`, in
worktree `C:\Users\Dell\Desktop\AuraEnterprise\aura-fullsuits-owner-ui`.

Method per workspace: `git rev-parse HEAD`, `git status --short`,
`git diff --stat` (unstaged + staged), SHA-256 of the raw diff output for
each, sorted untracked paths (`git ls-files --others --exclude-standard`)
and SHA-256 of that sorted list. Re-run identically at phase exit; any
difference is a violation of workspace isolation.

## aura-fullsuits (main / Unified Mobile workspace) — external to this phase

- Path: `C:\Users\Dell\Desktop\AuraEnterprise\aura-fullsuits`
- HEAD: `cff1991d5f6683edf341a6328effb160515e6548`
- Branch: `feat/retail-unified-mobile-android-ios`
- `git status --short`: empty
- unstaged diff --stat: empty
- staged diff --stat: empty
- unstaged-diff sha256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85`
- staged-diff sha256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85`
- untracked paths: none
- untracked-list sha256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85`

## aura-fullsuits-phase9r — external to this phase

- Path: `C:\Users\Dell\Desktop\AuraEnterprise\aura-fullsuits-phase9r`
- HEAD: `8c35768393d9407f72ef34aa93705f9c454453f7`
- Branch: `phase9r/real-secure-remote-production`
- `git status --short`: empty
- unstaged diff --stat: empty
- staged diff --stat: empty
- unstaged-diff sha256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85`
- staged-diff sha256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85`
- untracked paths: none
- untracked-list sha256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85`

## AuraEnterprise (legacy repository) — external to this phase

- Path: `C:\Users\Dell\Desktop\AuraEnterprise\AuraEnterprise`
- HEAD: `414e6ea5ca9fc698fe075a4ae4464cebf0b7bf34`
- Branch: `feat/crm-enterprise-lead-management`
- `git status --short`: **22 modified, 19 untracked** — pre-existing, real,
  unrelated in-progress work from another effort (a CRM/lead-management
  build). Not created by this task. Recorded, not touched.
  - Modified: `.gitignore`, `.superpowers/sdd/progress.md`, `api/auth.py`,
    `api/mt_auth.py`, `api/standalone_auth.py`,
    `api/subsystems/retail_api.py`, `api/system_download.py`, `app.py`,
    `config.py`, `core/crm/db.py`, `core/crm/domain.py`,
    `core/crm/dto/__init__.py`, `core/crm/services/__init__.py`,
    `core/crm/services/lead_assignment_service.py`,
    `core/crm/services/lead_lifecycle_service.py`,
    `core/crm/services/lead_management_validation_service.py`,
    `core/crm/services/lead_qualification_service.py`,
    `core/crm/services/lead_scoring_service.py`,
    `core/crm/services/lead_service.py`, `database/subsystem_db.py`,
    `scratch/seed_admin.py`, `static/js/subsystem-retail.js`
  - Untracked: `core/crm/repository/{lead_conversion,lead_duplicate,
    lead_merge,lead_timeline}.py`,
    `core/crm/services/{lead_conversion_service,lead_duplicate_service,
    lead_management_query_service,lead_merge_service,
    lead_normalization_service,lead_timeline_service}.py`,
    `core/retail/`, `core/security/`, `docs/retail/`,
    `tests/retail_pricing_test.py`, `tests/retail_security_test.py`
- unstaged-diff sha256: `cc37af7f7c95450e59be66bb4785a67bdfaeae18f7c86bbc2e3161ab76c66f7`
- staged-diff sha256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85`
- untracked-list sha256: `221ac6d0e3d5136d62885745dc294bbd59f43ef971d843bd35d343d695fca8a`

**This phase does not touch the legacy repository, Phase 9R worktree, or
the Unified Mobile workspace.** Exit fingerprints must match byte-for-byte
against the values recorded here.

## Verified regression baseline (entry state, this worktree)

Real, executed evidence, captured before any redesign work began.

**Invocation note:** `pytest products/clinic/tests` / `pytest
products/retail/tests` as raw directory-wide commands produce false
failures (`no such table: users`) — a known, pre-existing, documented
test-isolation artifact (`docs/licensing/phase7v/product-test-runner-
closure.md`): `AURA_APP_DATA` and related module-level constants are
resolved once per Python process at import time, so a single pytest
process importing multiple test files only honors the first file's
temp directory, and that file's `teardown_module` then deletes state
later files depend on. Reproduced identically even natively in the
pristine, untouched `aura-fullsuits-phase9r` worktree — confirmed
environment-independent, not a regression. The documented, correct
invocation is `products/run_all_tests.py` (per-file subprocess
isolation), used below for Retail/Clinic/commercial_runtime/
licensing_contracts. Owner is unaffected (its own `owner/tests/
conftest.py` uses a Postgres advisory lock precisely so its full suite
runs safely as one process) but required a worktree-local `.venv` —
fixed via a directory junction (`aura-fullsuits-owner-ui/.venv` →
`aura-fullsuits/.venv`) so `owner/tests/test_phase9_5e_dev_server_
port_isolation.py`'s real-subprocess dev-server test can find a venv
Python at its expected relative path, without duplicating the venv.

| Suite | Command | Result |
|---|---|---|
| Owner | `pytest owner/tests -q` | **1,041 passed**, 1 warning, 0 failed |
| commercial_runtime (own) | via `products/run_all_tests.py` | **5 passed** |
| licensing_contracts | via `products/run_all_tests.py` (22 files) | **230 passed** |
| Retail | via `products/run_all_tests.py` (12 files) | **194 passed** |
| Clinic | via `products/run_all_tests.py` (9 files) | **135 passed** |
| **Combined** | | **1,605 passed, 0 failed, 0 errors, 0 skipped** |

Matches the governing spec's required baseline (§3) exactly. This is
the number every later stage's regression check compares against —
the test count must not decrease from here.
