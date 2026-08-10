# aura-sync — automatic two-clone git sync

Keeps two local clones of this repo (yours and your teammate's) in sync without
either of you remembering to `git pull` / `git push`. It runs on an interval,
pulls what's new, commits and pushes what's local, and stops cleanly — never
silently — the moment it can't be sure it's safe to continue.

Engine: `scripts/sync/aura-sync.ps1` (Windows) / `scripts/sync/aura-sync.sh` (macOS/Linux/Git Bash).
Both implement the same algorithm, same config file, same exit codes, same log format.

## Quick start

**Windows:**
```powershell
# One-time: run pull-only for a day to see it work before trusting it with pushes.
.\scripts\sync\aura-sync.ps1 -Once

# Once you're comfortable, install it as a background scheduled task:
.\scripts\sync\aura-sync.ps1 -Install -IntervalMinutes 5
```

**macOS / Linux:**
```bash
chmod +x scripts/sync/aura-sync.sh
./scripts/sync/aura-sync.sh --once

./scripts/sync/aura-sync.sh --install --interval-minutes 5   # cron entry
```

That's it. Every 5 minutes (or whatever interval you pick) each of your clones
will: fetch, auto-commit anything you've changed, pull the other person's
changes, and push yours. Auto-sync is scoped to `master` by default -- opt
into tracking whatever branch you're on instead, see
["Tracking whatever branch you're on"](#tracking-whatever-branch-youre-on)
below.

## What it actually does, in order

1. **Locks** itself (so two passes on the same machine never overlap) and checks it's not **paused**.
2. **Snapshots** your current state to a recoverable git ref before touching anything.
3. **Fetches** `origin`.
4. **Auto-commits** your local changes — but only once they've been sitting still for
   90 seconds (so it never commits a half-saved file), and never for secrets, files over
   5 MB, or paths on the denylist (`.env`, `*.pem`, `*.jks`, `*.db`, etc.).
5. **Predicts conflicts before touching your files.** If pulling would conflict, it
   stops immediately — no merge is attempted, your working tree is untouched — writes
   `.autosync/CONFLICT.md`, pauses itself, and notifies you.
6. If it's clean, **integrates** (rebase onto the tracked branch's `origin/<branch>`
   by default) and **pushes**.
7. If your teammate pushed in the same instant, it retries once or twice with a short
   random delay before giving up for this pass (nothing is lost either way).
8. Writes a **confirmation summary** to the console, `.autosync/last-run.txt`, and (if
   anything happened or something went wrong) a desktop notification.

Every one of those steps is logged twice: a human-readable line in
`.autosync/logs/sync-YYYY-MM-DD.log`, and a structured JSON record in
`.autosync/logs/audit.jsonl` — the audit trail of every guard decision and git action
this machine has taken. Nothing in `.autosync/` is committed; it's gitignored and
per-machine only.

## Checking on it

```powershell
.\scripts\sync\aura-sync.ps1 -Status
```
```bash
./scripts/sync/aura-sync.sh --status
```

Shows the scheduled task/cron entry, the last pass's summary (what got pulled,
committed, pushed, or skipped), and whether it's currently paused.

You can also just read `.autosync\last-run.txt` (Windows) / `.autosync/last-run.txt`
(bash) directly — it's rewritten after every pass.

## Pausing before risky work

Doing an interactive rebase, a force-push, or anything else you don't want
auto-sync touching mid-operation? Pause it first:

```powershell
.\scripts\sync\aura-sync.ps1 -Pause -Minutes 30 -Reason "interactive rebase"
# ... do your risky thing ...
.\scripts\sync\aura-sync.ps1 -Resume
```
```bash
./scripts/sync/aura-sync.sh --pause --minutes 30 --reason "interactive rebase"
./scripts/sync/aura-sync.sh --resume
```

Omit `-Minutes`/`--minutes` for an indefinite pause (resume manually).
Auto-sync also pauses **itself** automatically when it hits a conflict, a
possible secret, or five failed passes in a row — always with a reason
recorded in the pause flag and the log.

## Resolving a conflict

When aura-sync predicts a conflict, it stops before merging anything. Your
files are exactly as you left them. Read `.autosync/CONFLICT.md` — it names
the branch, the conflicting files, and gives you the exact commands to
resolve manually:

```
git pull --no-rebase origin <branch>
# fix conflicts in the listed files
git add <resolved files>
git commit
git push origin <branch>
```

Then resume: `-Resume` / `--resume`. Recovery refs for anything auto-sync
snapshotted along the way are listed with `git for-each-ref refs/autosync/`
(never pushed, never visible in `git log`, safe to ignore if you don't need them).

## Configuration

Team defaults live in `scripts/sync/sync.config` (committed). Personal overrides
go in `.autosync/sync.config.local` (gitignored, never shared) — same `KEY=value`
format. Anything can also be overridden per-run with an `AURA_SYNC_<KEY>`
environment variable.

Key settings:

| Key | Default | Meaning |
|---|---|---|
| `INTERVAL_SECONDS` | 300 | How often `-Watch`/`--watch` re-syncs |
| `SYNC_BRANCHES` | `master` | `master` (fixed) \| `current` (track whatever's checked out) |
| `PULL_STRATEGY` | `rebase` | `rebase` \| `merge` \| `ff-only` |
| `AUTO_COMMIT` | `true` | Auto-commit local changes each pass |
| `AUTO_PUSH` | `true` | Auto-push after a clean integrate |
| `COMMIT_QUIET_SECONDS` | 90 | Debounce before auto-committing a dirty tree |
| `PATH_DENYLIST` | secrets/binaries | Never auto-staged, even if not gitignored |
| `SECRET_SCAN` | `true` | Regex scan of staged diffs for common credential formats |
| `PRE_PUSH_CHECK` | (empty) | Optional command; non-zero exit holds the push (commits stay local) |

If you'd rather review before it publishes anything, set `AUTO_PUSH=false` in
your local override — it'll still keep you up to date with your teammate's
pushes, it just won't publish yours automatically.

## Tracking whatever branch you're on

By default aura-sync only ever does anything while `master` is checked out —
on any other branch it silently skips the pass (`reason: branch-not-allowed:...`).
If your work lives on feature branches instead (e.g. one branch per phase),
that means aura-sync never touches it.

Opt into "sync whatever branch is currently checked out" instead:

```powershell
# One-off run:
.\scripts\sync\aura-sync.ps1 -TrackCurrentBranch

# Persistent, this clone only (create/edit .autosync\sync.config.local):
SYNC_BRANCHES=current

# Bake it into the installed scheduled task:
.\scripts\sync\aura-sync.ps1 -Install -IntervalMinutes 5 -TrackCurrentBranch
```
```bash
./scripts/sync/aura-sync.sh --track-current-branch
# persistent: SYNC_BRANCHES=current in .autosync/sync.config.local
./scripts/sync/aura-sync.sh --install --interval-minutes 5 --track-current-branch
```

What this changes:

- Every pass syncs **one** branch: whichever is checked out when that pass
  starts. It never syncs multiple branches in one pass.
- Switch branches between passes and the next pass just follows — no
  reconfiguration needed. If you check out a *different* branch mid-pass
  (during the jitter sleep or the fetch), that pass detects it and skips
  cleanly (`branch-changed-mid-pass`) rather than acting on stale state.
- Committing on a brand-new local branch that doesn't exist on `origin` yet
  auto-creates it there on first push (`--set-upstream`, never `--force`).
- Conflict prediction, the secret scan, the path denylist, and every other
  guard apply exactly the same as in `master` mode — nothing about the
  safety model changes, only which branch it runs against.
- `master`-only usage (the default, no opt-in) is completely unaffected.

## Scheduling reference

**Windows** — installs a per-user Scheduled Task (`AuraFullSuits-AutoSync`),
no admin required:
```powershell
.\scripts\sync\aura-sync.ps1 -Install -IntervalMinutes 5
.\scripts\sync\aura-sync.ps1 -Uninstall
```
Or run it in a terminal tab you keep open: `.\scripts\sync\aura-sync.ps1 -Watch`

**macOS / Linux** — `--install` adds a cron entry (works out of the box on both):
```bash
./scripts/sync/aura-sync.sh --install --interval-minutes 5
./scripts/sync/aura-sync.sh --uninstall
```
Cron intervals must divide 60 evenly (5, 10, 15, 20, 30) — for anything else,
or if you'd rather not touch your crontab, run `--watch` in a terminal tab, or
use one of these instead:

*macOS launchd* (`~/Library/LaunchAgents/com.aura.sync.plist`):
```xml
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
  <key>Label</key><string>com.aura.sync</string>
  <key>ProgramArguments</key>
  <array><string>/path/to/repo/scripts/sync/aura-sync.sh</string><string>--once</string></array>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
</dict></plist>
```
Load with `launchctl load ~/Library/LaunchAgents/com.aura.sync.plist`.

*Linux systemd user timer* (`~/.config/systemd/user/aura-sync.timer` +
matching `.service` running `aura-sync.sh --once`), `OnUnitActiveSec=5min`.
Enable with `systemctl --user enable --now aura-sync.timer`.

## Safety guarantees

- Never `git reset --hard`, `git clean -f`, `--force` push, or touch your
  uncommitted work destructively. Every pass snapshots to a recoverable
  `refs/autosync/*` ref first (never pushed, never in `git log`).
- Never auto-resolves a conflict. A predicted conflict always stops the pass
  before anything is merged.
- Never commits secrets it can pattern-match, oversized files, or anything on
  the denylist — even if the file isn't gitignored.
- Never runs two passes at once on the same machine (lock file), and retries
  cleanly if you and your teammate push at the same moment.
- Auto-pauses itself after repeated failures instead of retrying forever.
- Never force-pushes, and never pushes to any branch other than the one
  currently checked out -- including in `SYNC_BRANCHES=current` mode, where a
  diverged same-named remote branch is rejected non-fast-forward and handled
  through the normal fetch/integrate/retry path, same as any other push.

## Trade-offs, stated plainly

- **Auto-push publishes work-in-progress by design.** That's the point — it's
  what makes "sync" mean sync instead of "pull only." If you want a gate, set
  `PRE_PUSH_CHECK` to a test command, or `AUTO_PUSH=false` and push by hand.
- **History gets noisier** — auto-commits are prefixed `chore(autosync):` and
  folded into each other when they land within 15 minutes of an unpushed
  autosync commit, so it doesn't spam. Filter them out with:
  `git log --no-merges --invert-grep --grep='^chore(autosync)'`.
- **The most likely real failure is an expired GitHub credential.** aura-sync
  detects this, backs off for 30 minutes, and tells you to run
  `gh auth status` / `gh auth login` instead of hanging.
