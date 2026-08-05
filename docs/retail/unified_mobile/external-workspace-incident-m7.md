# External Workspace Incident Record (M7 Legacy-Repo Untracked-File Anomaly)

Real investigation of the anomaly disclosed in
`external-workspace-exit-fingerprints-m7.md`: the legacy
`C:\Users\Dell\Desktop\AuraEnterprise\AuraEnterprise` repository's
untracked-file count dropped from 19 (M7 entry) to 15 (M7 exit).
Investigated per the M7-acceptance checkpoint's own explicit
instructions — no speculation, no recreation, no Recycle Bin search,
no modification to the legacy repository.

## 1. Entry path list

**Not available.** `external-workspace-entry-fingerprints-m7.md` (and
every prior milestone's own fingerprint doc, `external-workspace-
entry-fingerprints-m6.md`/`external-workspace-exit-fingerprints-m6.md`)
recorded only a **count** ("19 untracked") and a **SHA-256 hash of the
untracked listing** (`UNTRACKED_LIST_HASH:
221ac6d0e3d5136d62885745dc294bbd59f43ef971d843bd35d343d695fca8a`) —
none of them ever recorded the literal file/directory paths. A SHA-256
hash cannot be reversed to recover the original list. This is a real,
honest limitation of the fingerprint protocol as it was actually
executed in every milestone through M7, not something this incident
investigation can work around — recorded here as fact rather than
worked around by guessing.

## 2. Exit path list (real, captured, reproducible)

15 untracked entries, unchanged between the M7-exit capture and a
fresh re-check performed for this incident record (identical
`UNTRACKED_LIST_HASH:
cafdb44a28f4865453bf28d1def21c1ca34496052ed638da5882a776bb4afe9f`
both times):

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

## 3. Missing paths

**Cannot be identified from existing written evidence.** Per item 1,
no document anywhere in this repository's history recorded the
literal 19-path list at M7 entry — only its hash. Without the
original list, the specific identity of "4 fewer" entries cannot be
computed by diffing two path lists; only the *count* delta (19 → 15)
and the *hash* delta are real, comparable facts. This report does not
guess at which 4 paths they were.

## 4. Tracked-file hash result

`DIFF_BINARY_HASH` (working-tree diff against `HEAD`) —
`cc37af7f7c95450e59be66bb4785a67bdfaeae18f7c86bbc2e3161ab76c66f7` —
identical at M7 entry, M7 exit, and a third re-check performed for
this incident record (three independent captures, same value). `git
diff --stat` also identical across all three captures: `22 files
changed, 1441 insertions(+), 260 deletions(-)`, same 22 file paths.
**Strong, repeated, real evidence that no tracked file's content
changed at any point across M7.**

## 5. Staged-diff result

`DIFF_CACHED_BINARY_HASH` —
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85`
(the real SHA-256 of an empty string) — identical at entry, exit, and
this re-check. Nothing was ever staged in this repository at any
point this session observed it.

## 6. Known read-only commands (this session's own record)

Every command this session issued against
`C:\Users\Dell\Desktop\AuraEnterprise\AuraEnterprise` end-to-end,
reviewed from this session's own tool-call history:

- `git rev-parse HEAD`
- `git status --short` (repeated, at entry, exit, and this incident
  re-check)
- `git diff` / `git diff --cached` (piped only to `sha256sum`, never
  to `apply`/`checkout`/any mutating flag)
- `git log -1 --format=%cd -- <path>` (for a handful of the
  now-untracked paths, checking git-log dates — returned empty since
  these paths were never committed)
- `stat` (filesystem metadata read only)

No `git add`, `git rm`, `git checkout`, `git restore`, `git reset`,
`git clean`, `git stash`, `git commit`, or any shell `rm`/`mv`/`del`
command was ever issued against this path. This is a complete,
exhaustive list — not a sample.

## 7. Attribution status

**UNATTRIBUTABLE_TO_THIS_SESSION.** Supporting evidence:
- Zero write/mutating commands were ever issued against this path (item 6).
- The 15 remaining untracked entries' own filesystem modification
  timestamps (`core/retail/`, `core/security/`, `docs/retail/`) are
  dated 2026-07-12 — three weeks before M7 began — confirming the
  surviving untracked material itself long predates this milestone.
- Tracked-file content is proven byte-identical across three
  independent captures spanning the entire M7 window (item 4).

None of this proves what *did* cause the count to drop from 19 to 15
— it only rules out this session's own tool calls as the cause. The
true cause remains genuinely unknown.

## 8. Current risk

**Low, but not zero, and not fully closed.** The substantive risk this
fingerprint protocol exists to catch — this session silently editing
or destroying real work in an external repository — is well-evidenced
as absent (item 4, item 6). The residual, unresolved risk is narrower:
some untracked file(s) in a workspace outside this session's control
may have been altered or removed by something else (another tool,
editor autosave/cleanup, antivirus quarantine, a sync client, or a
separate concurrent session) during the M7 window, and this
investigation cannot rule that out or characterize it further from
the evidence available.

## 9. Manual investigation recommendation

Recommended for the user, outside this session's own tools (per the
checkpoint's own instruction not to search Recycle Bin or alter the
legacy repository automatically):
- Check Windows File History / any configured backup tool for
  `C:\Users\Dell\Desktop\AuraEnterprise\AuraEnterprise` around the M7
  window.
- Check whether any other editor/IDE/session had this path open
  during the same window.
- Check antivirus/EDR quarantine logs for this path.
- If a local backup or shadow copy exists, compare its untracked-file
  listing against the 15-item list in item 2 above to identify exactly
  which paths differ.

## 10. Decision: use current state as the new M8 entry baseline

Per the M7-acceptance checkpoint's own explicit instruction, this
incident is not resolved by recreating, restoring, or otherwise
altering the legacy workspace. `external-workspace-entry-fingerprints-
m8.md` captures a fresh baseline from the *current, real* state (the
15-item list above), and M8 exit will be compared against that new
baseline. M8 will claim `UNCHANGED_RELATIVE_TO_M8_ENTRY_STATE`, never
`UNCHANGED_RELATIVE_TO_M7_ENTRY_STATE` — the M7 baseline is
permanently superseded by this incident, not retroactively repaired.
