# External Workspace Entry Fingerprints (M6 Entry)

Real, executed fingerprints captured at the exact start of M6 (Unified
Mobile branch HEAD `c210bb1a750844b11095a7955703bdaf11f02200` at
capture time, `feat/retail-unified-mobile-android-ios`, `git status
--short` confirmed clean before this capture). Recorded as observed
state, not asserted clean or byte-identical — same discipline as
every prior milestone's own capture.

## `C:\Users\Dell\Desktop\AuraEnterprise\AuraEnterprise` (legacy repo)

```
HEAD: 414e6ea5ca9fc698fe075a4ae4464cebf0b7bf34   (unchanged since M5.8 entry/exit)

STATUS (git status --short): 22 modified, 19 untracked (identical file list to M5.8's captures)

DIFFSTAT (git diff --stat): 22 files changed, 1441 insertions(+), 260 deletions(-)

DIFF_BINARY_HASH:        cc37af7f7c95450e59be66bb4785a67bdfaeae18f7c86bbc2e3161ab76c66f7
DIFF_CACHED_BINARY_HASH: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85   (empty, nothing staged)

UNTRACKED_LIST_HASH: 221ac6d0e3d5136d62885745dc294bbd59f43ef971d843bd35d343d695fca8a
```

Every value is byte-identical to `external-workspace-exit-fingerprints-m5-8.md`'s
capture — the same real, pre-existing, unrelated dirty state (last
real commit 2026-07-11), still untouched.

## `C:\Users\Dell\Desktop\AuraEnterprise\aura-fullsuits-phase9r`

```
HEAD: 8c35768393d9407f72ef34aa93705f9c454453f7   (unchanged since M5.8 entry/exit)

STATUS: (empty)
DIFFSTAT: (empty)
DIFF_BINARY_HASH / DIFF_CACHED_BINARY_HASH: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85   (empty)
UNTRACKED_LIST_HASH: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85   (empty)
```

Identical to M5.8's own exit capture — no real Phase 9R work has
landed between the two milestones' fingerprint checks.

## What "unchanged" will mean at M6 exit

At M6 exit, the same 8 real commands will be re-run against both
paths. Required conclusion:
`UNCHANGED_RELATIVE_TO_M6_ENTRY_STATE` — every value above must match
exactly. No command in this milestone stages, restores, resets,
cleans, or commits inside either external workspace.
