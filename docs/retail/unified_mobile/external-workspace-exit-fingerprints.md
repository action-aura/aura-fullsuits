# External Workspace Exit Fingerprints (M5.7 Exit)

Real, executed fingerprints of both external workspaces, re-captured at
M5.7 exit (Unified Mobile branch HEAD `79128b6` at capture time,
`feat/retail-unified-mobile-android-ios`), using the exact same 8
read-only commands as `external-workspace-entry-fingerprints.md`. No
command in this milestone staged, restored, reset, cleaned, or committed
inside either external workspace.

## Result: `UNCHANGED_RELATIVE_TO_M5_7_ENTRY_STATE` for both

## `C:\Users\Dell\Desktop\AuraEnterprise\AuraEnterprise` (legacy repo)

```
HEAD: 414e6ea5ca9fc698fe075a4ae4464cebf0b7bf34   (unchanged)

STATUS: 22 modified, 19 untracked                (unchanged, identical file list)

DIFFSTAT: 22 files changed, 1441 insertions(+), 260 deletions(-)   (unchanged)

DIFF_BINARY_HASH:        cc37af7f7c95450e59be66bb4785a67bdfaeae18f7c86bbc2e3161ab76c66f7   (unchanged)
DIFF_CACHED_BINARY_HASH: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85   (unchanged, empty)

UNTRACKED_LIST_HASH: 221ac6d0e3d5136d62885745dc294bbd59f43ef971d843bd35d343d695fca8a   (unchanged)
```

Every value is byte-identical to the M5.7-entry capture
(`external-workspace-entry-fingerprints.md`). The pre-existing dirty
state (unrelated to this session, last real commit 2026-07-11) is
exactly as it was at M5.7 entry — real, verified, not assumed.

## `C:\Users\Dell\Desktop\AuraEnterprise\aura-fullsuits-phase9r`

```
HEAD: 8c35768393d9407f72ef34aa93705f9c454453f7   (unchanged)

STATUS: (empty)                                   (unchanged)
DIFFSTAT: (empty)                                 (unchanged)
DIFF_BINARY_HASH / DIFF_CACHED_BINARY_HASH: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85   (unchanged, empty)
UNTRACKED_LIST_HASH: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85   (unchanged, empty)
```

Every value is byte-identical to the M5.7-entry capture. No real Phase
9R work landed between the two captures (unsurprising — Phase 9R's own
work happens in a separate session/workstream, and this M5.7 session
never referenced or touched that path).

## Verdict

Both external workspaces are `UNCHANGED_RELATIVE_TO_M5_7_ENTRY_STATE` —
the required claim, not `CLEAN` (the legacy repo is not clean, and was
never claimed to be) and not `BYTE_IDENTICAL_TO_HEAD` (also not claimed
— `AuraEnterprise`'s real uncommitted changes remain exactly as they
were, neither erased nor added to).
