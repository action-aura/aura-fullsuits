# External Workspace Entry Fingerprints (M8 Entry)

Real, executed fingerprints captured at the exact start of M8 (Unified
Mobile branch HEAD `2491308ca9b1ad301e4a0cb769fbb2c0646d14ce`,
`feat/retail-unified-mobile-android-ios`, `git status --short`
confirmed clean before this capture). Per
`external-workspace-incident-m7.md`'s own decision, this baseline
supersedes the M7 baseline — M8 exit compares against this document,
never against M7 entry.

## `C:\Users\Dell\Desktop\AuraEnterprise\aura-fullsuits-phase9r`

```
HEAD: 8c35768393d9407f72ef34aa93705f9c454453f7   (unchanged since M6/M7 entry and exit)

STATUS: (empty, 0 lines)
DIFFSTAT: (empty)
DIFF_BINARY_HASH / DIFF_CACHED_BINARY_HASH / UNTRACKED_LIST_HASH:
  e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85 (empty, all three)
```

## `C:\Users\Dell\Desktop\AuraEnterprise\AuraEnterprise` (legacy repo)

```
HEAD: 414e6ea5ca9fc698fe075a4ae4464cebf0b7bf34   (unchanged since M6/M7 entry and exit)

STATUS (git status --short): 22 modified, 15 untracked

DIFFSTAT (git diff --stat): 22 files changed, 1441 insertions(+), 260 deletions(-)

DIFF_BINARY_HASH:        cc37af7f7c95450e59be66bb4785a67bdfaeae18f7c86bbc2e3161ab76c66f7
DIFF_CACHED_BINARY_HASH: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85   (empty, nothing staged)

UNTRACKED_LIST_HASH: cafdb44a28f4865453bf28d1def21c1ca34496052ed638da5882a776bb4afe9f
```

Real, literal untracked path list at this exact M8-entry capture (see
`external-workspace-incident-m7.md` item 2 for the same list, captured
moments earlier for the incident investigation — reproduced here as
the actual M8 baseline, not merely referenced, so this document is
self-sufficient for the M8 exit comparison):

```
?? core/crm/repository/lead_conversion.py
?? core/crm/repository/lead_duplicate.py
?? core/crm/repository/lead_merge.py
?? core/crm/repository/lead_timeline.py
?? core/crm/services/lead_conversion_service.py
?? core/crm/services/lead_duplicate_service.py
?? core/crm/services/lead_management_query_service.py
?? core/crm/services/lead_merge_service.py
?? core/crm/services/lead_normalization_service.py
?? core/crm/services/lead_timeline_service.py
?? core/retail/
?? core/security/
?? docs/retail/
?? tests/retail_pricing_test.py
?? tests/retail_security_test.py
```

Tracked-file diff content (`DIFF_BINARY_HASH`) is the same real,
pre-existing, unrelated dirty state carried since before M6 (last
real commit 2026-07-11) — unchanged in substance, only the untracked
count differs from the pre-M7 baseline, per the incident record above.

## What "unchanged" will mean at M8 exit

At M8 exit, the same 8 real commands will be re-run against both
paths, plus a literal untracked-path-list capture (not merely a hash)
for the legacy repo, so a future incident (if any) can be diffed
directly against this document without repeating M7's own limitation.
Required conclusion: `UNCHANGED_RELATIVE_TO_M8_ENTRY_STATE` — every
value above, including the 15-item literal path list, must match
exactly. No command in this milestone stages, restores, resets,
cleans, or commits inside either external workspace.
