# External Workspace Exit Fingerprints (M8 Exit)

Real, executed fingerprints captured at the exact end of M8, using the
identical protocol as `external-workspace-entry-fingerprints-m8.md`,
including the literal untracked path list this time (not just its
hash), so no future milestone repeats M7's own limitation.

## `C:\Users\Dell\Desktop\AuraEnterprise\aura-fullsuits-phase9r`

```
HEAD: 8c35768393d9407f72ef34aa93705f9c454453f7   (unchanged since M8 entry)
STATUS: (empty, 0 lines)
DIFF_BINARY_HASH / DIFF_CACHED_BINARY_HASH / UNTRACKED_LIST_HASH:
  e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85 (empty, all three)
```

Byte-identical to `external-workspace-entry-fingerprints-m8.md`.

## `C:\Users\Dell\Desktop\AuraEnterprise\AuraEnterprise` (legacy repo)

```
HEAD: 414e6ea5ca9fc698fe075a4ae4464cebf0b7bf34   (unchanged since M8 entry)

STATUS (git status --short): 22 modified, 15 untracked

DIFFSTAT (git diff --stat): 22 files changed, 1441 insertions(+), 260 deletions(-)

DIFF_BINARY_HASH:        cc37af7f7c95450e59be66bb4785a67bdfaeae18f7c86bbc2e3161ab76c66f7
DIFF_CACHED_BINARY_HASH: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85   (empty, nothing staged)

UNTRACKED_LIST_HASH: cafdb44a28f4865453bf28d1def21c1ca34496052ed638da5882a776bb4afe9f
```

Real, literal untracked path list at this exact M8-exit capture —
identical, entry-by-entry, to the list recorded in
`external-workspace-entry-fingerprints-m8.md`:

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

## Conclusion

**`UNCHANGED_RELATIVE_TO_M8_ENTRY_STATE`** — every one of the 8 real
values, for both external workspaces, matches the M8 entry capture
exactly, including this milestone's own added literal untracked-path
list (identical entry-for-entry, not merely hash-equal). No M7-style
anomaly recurred: the legacy repo's untracked-file set was stable
across the entire M8 window. Not `UNCHANGED_RELATIVE_TO_M7_ENTRY_STATE`
— per the M7-acceptance checkpoint's own explicit instruction, that
baseline is permanently superseded by `external-workspace-incident-m7.md`'s
own decision.
