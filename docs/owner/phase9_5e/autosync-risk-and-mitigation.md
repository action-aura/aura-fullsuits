# Phase 9.5E Milestone 0 — `.autosync` Investigation, Risk, and Mitigation

## What ".autosync" actually is

Not external, not mysterious: it is real, in-repo tooling at `scripts/sync/` on the `master` branch (`aura-sync.ps1` + `lib/{Config,Audit,Notify,State,Guards,GitOps,Scheduler}.ps1` + `sync.config`), driven by a Windows Scheduled Task named `AuraFullSuits-AutoSync` (`schtasks`/`Get-ScheduledTask` confirmed: runs `aura-sync.ps1 -Once` every 5 minutes, `DisallowStartIfOnBatteries`, `MultipleInstances=IgnoreNew`). `.autosync/` at the repo root is its gitignored runtime directory (logs, lock, state, snapshot refs) — confirmed via `git status --ignored=matching .autosync` (`!!` = ignored), not a hidden config file.

**It was authored today, on `master`, by the repo owner** — commit `62ca2a1` ("feat: add aura-sync two-way git auto-sync for shared local clones"), 2026-08-02 12:58:51 +0300, ~40 minutes before this phase's Entry Gate ran. `git log --all -- scripts/sync/` shows exactly one commit, ever. It is real infrastructure for syncing this repo across the user's own multiple clones/machines — not something built or discovered by any prior Owner-phase session.

## Verified behavior (read from the actual guard code, not assumed from comments)

`sync.config` (team defaults, committed): `SYNC_BRANCHES=master`, `AUTO_COMMIT=true`, `AUTO_PUSH=true`, `PULL_STRATEGY=rebase`, `SECRET_SCAN=true`, `PATH_DENYLIST=*.env,*.pem,*.key,...`, `CHURN_LIMIT=5`, `MAX_FILE_KB=5120`.

`lib/Guards.ps1` — every pass runs behind named, audited guards, fail-closed:
- **`Test-BranchGuard`**: resolves `git symbolic-ref --short HEAD`; if the branch is not in `SYNC_BRANCHES` (i.e. not literally `master`) the pass is skipped entirely — no commit, no push, no checkout, nothing. Detached HEAD also fails closed.
- **`Enter-SyncLock`**: directory-based lock with PID+hostname ownership file and staleness+liveness reclaim (won't steal a live lock).
- **`Test-InProgressGuard`**: skips if a rebase/merge/cherry-pick is already mid-flight.
- **`Test-SecretScan`**: regex-scans the staged diff for AWS/GitHub/Slack/generic API-key/private-key patterns before ever committing; blocks and pauses on a hit.
- Conflict handling: `git merge-tree` predicts a conflict **before touching the working tree**; on a predicted conflict it writes `.autosync/CONFLICT.md`, sets a pause flag, and stops — never force-merges.
- `New-SyncSnapshot` records a backup ref before any mutating step, so any pass is forensically recoverable via `refs/autosync/`.
- Auth failures and repeated failures both trigger real backoff/auto-pause (`MAX_CONSECUTIVE_FAILURES=5`).

Structurally, this tool **cannot** act on any Owner-phase feature branch (`phase9.5/...`) — the branch guard is the very first content check in the pass and is a hard skip, confirmed by reading the code, not just the log lines.

## Reconciling this with the Phase 9.5D-documented incidents

Phase 9.5D's own closure (`docs/owner/phase9_5d/phase9-5d-final-decision.md`) recorded three real working-tree-discard incidents attributed to "`.autosync`" during Milestones 14–15, recovered via `git reflog`. Those incidents predate this tool's only commit by days. Since `git log --all` shows no earlier commit ever touching `scripts/sync/`, **whatever caused those incidents was not this tool** — it did not exist yet. The true root cause of the M14/M15 incidents remains unrecovered and is not this phase's to re-investigate further; what matters going forward is that the mechanism now actually present on this machine is this one, and it is verified safe-by-construction for feature-branch work.

## Residual risk (real, not hypothetical) and mitigation

The one live hazard this tool does introduce: **if the `aura-fullsuits` working tree is ever left checked out on `master` with uncommitted changes for a full 5-minute window, `aura-sync` will auto-commit them (`chore(autosync)` prefix) and auto-push to `origin/master`** — silently publishing whatever was sitting there, since `AUTO_PUSH=true` is a committed team default. This is real and currently armed on this machine right now.

Mitigation (process, not a code change to the user's own just-authored shared tool — no defect was found in it to justify editing `scripts/sync/sync.config`, which is team-shared infrastructure affecting every clone):
- Never leave the repo checked out on `master` mid-task. Every Owner-phase session already switches to a phase feature branch immediately (this session did so via `git switch -c phase9.5/expenses-reporting-management-collaboration <tag>` before any file write).
- Verify `git branch --show-current` immediately before any git operation that assumes a specific branch — the existing Phase 9.5D-adopted discipline (frequent, small commits; branch check before commit) already covers this and needed no change.
- If a session must genuinely work on `master` (e.g. a hotfix), run `.\scripts\sync\aura-sync.ps1 -Pause -Reason "<why>"` first — the tool ships its own pause/resume CLI for exactly this.

No code in `scripts/sync/` was modified by this milestone. It was audited, not altered — it is already fail-closed, already logs every guard decision to `.autosync/logs/`, and already refuses to act outside `master`.
