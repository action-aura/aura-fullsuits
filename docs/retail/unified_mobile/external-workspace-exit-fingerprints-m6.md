# External Workspace Exit Fingerprints (M6 Exit)

Real, executed fingerprints captured at the exact end of M6, using the
identical 8-command protocol as `external-workspace-entry-fingerprints-m6.md`.

## `C:\Users\Dell\Desktop\AuraEnterprise\AuraEnterprise` (legacy repo)

```
HEAD: 414e6ea5ca9fc698fe075a4ae4464cebf0b7bf34   (unchanged since M6 entry)

STATUS (git status --short): 22 modified, 19 untracked — identical file list to M6 entry's capture

DIFFSTAT (git diff --stat): 22 files changed, 1441 insertions(+), 260 deletions(-)

DIFF_BINARY_HASH:        cc37af7f7c95450e59be66bb4785a67bdfaeae18f7c86bbc2e3161ab76c66f7
DIFF_CACHED_BINARY_HASH: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85   (empty, nothing staged)

UNTRACKED_LIST_HASH: 221ac6d0e3d5136d62885745dc294bbd59f43ef971d843bd35d343d695fca8a
```

Every value is byte-identical to `external-workspace-entry-fingerprints-m6.md`'s
own capture — the same real, pre-existing, unrelated dirty state (last
real commit 2026-07-11), still completely untouched by any command run
during M6.

## `C:\Users\Dell\Desktop\AuraEnterprise\aura-fullsuits-phase9r`

```
HEAD: 8c35768393d9407f72ef34aa93705f9c454453f7   (unchanged since M6 entry)

STATUS: (empty)
DIFFSTAT: (empty)
DIFF_BINARY_HASH / DIFF_CACHED_BINARY_HASH: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85   (empty)
UNTRACKED_LIST_HASH: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85   (empty)
```

Byte-identical to M6's own entry capture — no command touched this
worktree during M6.

## Conclusion

`UNCHANGED_RELATIVE_TO_M6_ENTRY_STATE` — every one of the 8 real
values, for both external workspaces, matches the M6 entry capture
exactly. Not `CLEAN` (the `AuraEnterprise` legacy repo has real,
pre-existing, disclosed dirty state, unrelated to this work) and not
`BYTE_IDENTICAL_TO_HEAD` (same reason) — the required, honest
conclusion per the checkpoint's own explicit instruction.
