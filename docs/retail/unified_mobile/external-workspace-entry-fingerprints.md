# External Workspace Entry Fingerprints (M5.7 Entry)

Real, executed fingerprints of the two external workspaces this
initiative must never modify, captured at the exact start of M5.7 (Unified
Mobile branch HEAD `8eb0c5ece20560a629be82c79d7bcfbef3327ac5` at capture
time, `git branch --show-current` confirmed `feat/retail-unified-mobile-android-ios`,
`git status --short` confirmed clean before this capture).

Per the M5.7 external-workspace-preservation rule: these are recorded as
**observed state**, not asserted as clean or byte-identical. Neither
workspace was staged, restored, reset, cleaned, or committed by this
capture — every command below is read-only (`status`/`diff`/`ls-files`/
`rev-parse`).

## `C:\Users\Dell\Desktop\AuraEnterprise\AuraEnterprise` (legacy repo)

```
HEAD: 414e6ea5ca9fc698fe075a4ae4464cebf0b7bf34

STATUS (git status --short): 22 modified, 19 untracked
  M .gitignore
  M .superpowers/sdd/progress.md
  M api/auth.py
  M api/mt_auth.py
  M api/standalone_auth.py
  M api/subsystems/retail_api.py
  M api/system_download.py
  M app.py
  M config.py
  M core/crm/db.py
  M core/crm/domain.py
  M core/crm/dto/__init__.py
  M core/crm/services/__init__.py
  M core/crm/services/lead_assignment_service.py
  M core/crm/services/lead_lifecycle_service.py
  M core/crm/services/lead_management_validation_service.py
  M core/crm/services/lead_qualification_service.py
  M core/crm/services/lead_scoring_service.py
  M core/crm/services/lead_service.py
  M database/subsystem_db.py
  M scratch/seed_admin.py
  M static/js/subsystem-retail.js

DIFFSTAT (git diff --stat): 22 files changed, 1441 insertions(+), 260 deletions(-)

DIFF_BINARY_HASH (git diff --binary | sha256sum):
  cc37af7f7c95450e59be66bb4785a67bdfaeae18f7c86bbc2e3161ab76c66f7

DIFF_CACHED_BINARY_HASH (git diff --cached --binary | sha256sum):
  e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85  (empty diff -- nothing staged)

UNTRACKED_FILES (git ls-files --others --exclude-standard):
  core/crm/repository/lead_conversion.py
  core/crm/repository/lead_duplicate.py
  core/crm/repository/lead_merge.py
  core/crm/repository/lead_timeline.py
  core/crm/services/lead_conversion_service.py
  core/crm/services/lead_duplicate_service.py
  core/crm/services/lead_management_query_service.py
  core/crm/services/lead_merge_service.py
  core/crm/services/lead_normalization_service.py
  core/crm/services/lead_timeline_service.py
  core/retail/__init__.py
  core/retail/pricing.py
  core/security/__init__.py
  core/security/app_secret.py
  core/security/audit.py
  core/security/modes.py
  core/security/passwords.py
  docs/retail/RETAIL_SECURITY_PHASE_1.md
  tests/retail_pricing_test.py
  tests/retail_security_test.py

UNTRACKED_LIST_HASH (sorted paths | sha256sum):
  221ac6d0e3d5136d62885745dc294bbd59f43ef971d843bd35d343d695fca8a
```

This dirty state was already disclosed in `milestone-5-6-decision.md`
(last real commit dated 2026-07-11, well before this session) — this is
the same state, re-captured as the formal M5.7-entry fingerprint per the
new rule, not a new finding.

## `C:\Users\Dell\Desktop\AuraEnterprise\aura-fullsuits-phase9r`

```
HEAD: 8c35768393d9407f72ef34aa93705f9c454453f7

STATUS (git status --short): (empty -- no modified or untracked files at capture time)

DIFFSTAT (git diff --stat): (empty)

DIFF_BINARY_HASH (git diff --binary | sha256sum):
  e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85  (empty diff)

DIFF_CACHED_BINARY_HASH (git diff --cached --binary | sha256sum):
  e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85  (empty diff)

UNTRACKED_FILES (git ls-files --others --exclude-standard): (none)

UNTRACKED_LIST_HASH (sorted paths | sha256sum):
  e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85  (empty list)
```

Note: `HEAD` here (`8c35768...`) differs from the commit last observed
during M5.6 closeout (`b5dd17aa...`) — real, independent Phase 9R work
landed a commit between M5.6 closeout and this M5.7-entry capture. This
is expected and outside this session's scope (Phase 9R is worked
separately); it is recorded honestly as the real entry point M5.7's exit
fingerprint must be compared against, not as evidence of any action by
this session.

## What "unchanged" will mean at M5.7 exit

At M5.7 exit, the same eight commands will be re-run against both paths.
The required claim is `UNCHANGED_RELATIVE_TO_M5_7_ENTRY_STATE`: every
value above (`HEAD`, `STATUS`, `DIFFSTAT`, both diff hashes, the
untracked file list, and its hash) must match exactly. Any difference is
reported as a real finding, not silently reconciled — no command in this
initiative stages, restores, resets, cleans, or commits inside either
external workspace.
