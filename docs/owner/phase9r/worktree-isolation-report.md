# Phase 9R — Worktree Isolation Report (M0)

## Command and result

```
$ cd C:\Users\Dell\Desktop\AuraEnterprise\aura-fullsuits
$ git worktree add -b phase9r/real-secure-remote-production ^
    C:\Users\Dell\Desktop\AuraEnterprise\aura-fullsuits-phase9r ^
    aura-owner-expenses-reporting-phase9-5e-complete

Preparing worktree (new branch 'phase9r/real-secure-remote-production')
Updating files: 100% (1661/1661), done.
HEAD is now at bd12681 Phase 9.5E: final gate matrix, decision, risk register, and handover
```

## Verification, from inside the new worktree

```
$ cd C:\Users\Dell\Desktop\AuraEnterprise\aura-fullsuits-phase9r
$ git status --short
(clean — no output)

$ git branch --show-current
phase9r/real-secure-remote-production

$ git rev-parse HEAD
bd126818153de019eaf94c7a3996a3e252afdae2

$ git worktree list
C:/Users/Dell/Desktop/AuraEnterprise/aura-fullsuits         2c6824a [feat/retail-unified-mobile-android-ios]
C:/Users/Dell/Desktop/AuraEnterprise/aura-fullsuits-phase9r bd12681 [phase9r/real-secure-remote-production]

$ ls mobile
ls: cannot access 'mobile': No such file or directory
```

## What this proves

| Claim | Evidence |
|---|---|
| Phase 9R starts at the exact Phase 9.5E tag commit | `HEAD` = `bd126818...`, identical to `git rev-list -n 1 aura-owner-expenses-reporting-phase9-5e-complete` |
| Worktree is clean at creation | `git status --short` empty |
| Two independent worktrees exist, on two independent branches | `git worktree list` shows both paths with distinct branches and distinct HEAD commits |
| No unified-mobile work reached Phase 9R | The `mobile/` directory doesn't exist in this worktree at all — that work was added to the repo *after* the Phase 9.5E tag, so a checkout of that tag structurally cannot contain it. This isn't a `.gitignore` exclusion or a manual deletion; it's a direct consequence of cutting the worktree from an older commit. |
| Main workspace unaffected | `git worktree add` operates only on the new worktree's checkout and a new branch ref; it does not read, stage, or modify the working tree, index, or HEAD of the workspace it's invoked from. The primary workspace's uncommitted mobile changes (`build.gradle.kts` modified, new Kotlin financial sources) were never touched. |

## Autosync exposure

`scripts/sync/sync.config` scopes `aura-sync` to `SYNC_BRANCHES=master` only.
The Phase 9R branch (`phase9r/real-secure-remote-production`) is not `master`,
so the installed `AuraFullSuits-AutoSync` scheduled task — which operates
against the **primary** worktree's checkout of `master` — has no path to this
worktree's branch at all: different directory, different branch, and outside
the configured sync scope on both counts. Confirmed by inspection of
`scripts/sync/sync.config` (`SYNC_BRANCHES=master`) inherited unchanged from
the Phase 9.5E baseline this worktree was cut from.

## Verdict

**PASS.** Isolation is structural (separate worktree, separate branch, cut
from a commit that predates the concurrent work), not merely procedural.
