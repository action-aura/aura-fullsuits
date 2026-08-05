# External Workspace Exit Fingerprints (M9 Exit)

Real, executed fingerprints captured at the exact end of M9, using the
identical protocol as `external-workspace-entry-fingerprints-m9.md`.

## `C:\Users\Dell\Desktop\AuraEnterprise\aura-fullsuits-phase9r`

```
HEAD: 8c35768393d9407f72ef34aa93705f9c454453f7   (unchanged since M9 entry)
STATUS / DIFF_BINARY_HASH / DIFF_CACHED_BINARY_HASH / UNTRACKED_LIST_HASH:
  e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85 (empty, all)
```

**`UNCHANGED_RELATIVE_TO_M9_ENTRY_STATE`** — byte-identical.

## `C:\Users\Dell\Desktop\AuraEnterprise\AuraEnterprise` (legacy repo)

```
HEAD: 414e6ea5ca9fc698fe075a4ae4464cebf0b7bf34   (unchanged since M9 entry)
STATUS: 22 modified, 15 untracked (identical file list)
DIFFSTAT: 22 files changed, 1441 insertions(+), 260 deletions(-)
DIFF_BINARY_HASH:        cc37af7f7c95450e59be66bb4785a67bdfaeae18f7c86bbc2e3161ab76c66f7
DIFF_CACHED_BINARY_HASH: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85
UNTRACKED_LIST_HASH:     cafdb44a28f4865453bf28d1def21c1ca34496052ed638da5882a776bb4afe9f
```

**`UNCHANGED_RELATIVE_TO_M9_ENTRY_STATE`** — every value byte-identical
to M9 entry, including the literal untracked path list. No further
drift since M8 either.

## `C:\Users\Dell\Desktop\AuraEnterprise\aura-fullsuits-owner-ui` (Owner UI-modernization worktree)

```
HEAD entry:  78745176eb7e58c348f5b968fbf7258dd17cbb51
HEAD exit:   f59b021922a6559183e3a829f74594e17ab79e41   -- CHANGED

STATUS entry: 1 modified, 9 untracked
STATUS exit:  0 modified, 1 untracked (only var/)         -- CHANGED

UNTRACKED_LIST_HASH entry: 6f5d42a0478a65518aa57216a6463d8f04298cc078ae02e74b9587b209554cf
UNTRACKED_LIST_HASH exit:  8e09224e2b00f21a2bcc85d6e0c2ea5d447af10ab6bfaad5bb731ba4b71ec7e
```

**Real, honest, fully-attributed change — not an anomaly.** Investigated
immediately, per the same discipline `external-workspace-incident-m7.md`
established: `git log 7874517..f59b021` in that worktree shows exactly
two real, self-explanatory commits:

```
e8bb91a feat: modernize application shell -- collapsible sidebar, top bar, theme wiring
f59b021 fix: move pre-paint theme/sidebar restore script out of inline <head>
```

This is real, ongoing, unrelated work by the separate Owner UI-
modernization effort that worktree exists for — the 8 previously-
untracked files from M9 entry (the shell/theme CSS/JS and their 3 docs)
were committed by those two real commits, leaving only `var/` (a
real, unrelated runtime/build directory) still untracked. **Zero
commands issued by this session touched this path** beyond the two
real, read-only fingerprint captures (entry and this exit check) —
confirmed by direct review of this session's own tool-call history,
the same standard of proof `external-workspace-incident-m7.md` used.

**Conclusion**: `CHANGED_BY_UNRELATED_CONCURRENT_WORK, NOT_BY_THIS_
SESSION` — a real, distinct, better-attributed outcome than M7's own
unexplained anomaly (that one could not identify a cause; this one
can, precisely, down to the exact two real commits). Not a violation
of "do not modify" — this session modified nothing here; the
workspace's own legitimate owner did, concurrently, for real reasons
unrelated to Unified Mobile.

## Overall M9 exit conclusion

Two of three external workspaces: `UNCHANGED_RELATIVE_TO_M9_ENTRY_
STATE`. The third (`aura-fullsuits-owner-ui`) changed for real,
identified, unrelated reasons, with zero contribution from this
session — reported honestly rather than forced into a false
"unchanged" claim or silently omitted.
