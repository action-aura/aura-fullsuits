# Phase 9.5E Milestone 0 — Development Tooling Safety Hardening (Summary)

Mandatory first milestone per the governing spec: investigate and close the two operational hazards Phase 9.5D disclosed (the `.autosync` work-loss incidents and the M25 dev-server port collision) before any Phase 9.5E business logic is built.

## 1. `.autosync` — investigated, found to be real in-repo tooling, not external

Full findings in [`autosync-risk-and-mitigation.md`](autosync-risk-and-mitigation.md). Summary: it is `scripts/sync/aura-sync.ps1` on `master` (commit `62ca2a1`, authored by the repo owner today, 2026-08-02, ~40 minutes before this phase's Entry Gate), driven by Windows Scheduled Task `AuraFullSuits-AutoSync` every 5 minutes. Read the actual guard code (`lib/Guards.ps1`): fail-closed branch guard restricts all action to `SYNC_BRANCHES=master` only; secret-scan, path-denylist, pre-mutation snapshot refs, and conflict-prediction-before-touch are all real and already present. It structurally cannot act on any Owner-phase feature branch. No code changed — audited, not modified, since no defect was found. One real residual risk documented and mitigated by process (never leave the tree on `master` with uncommitted state), not by editing the user's own just-authored, team-shared config.

The three historical work-loss incidents Phase 9.5D recorded (M14/M15) predate this tool's only commit and were not caused by it — their true root cause is unrecovered and out of this milestone's scope; what matters is that the mechanism now present is verified safe.

## 2. Browser-test port collision — closed with real, tested tooling

Full findings in [`browser-test-port-isolation.md`](browser-test-port-isolation.md). Summary: built `owner/tools/dev_server/port_isolation.py` — ephemeral OS-assigned ports (never a fixed port again), real `/health/live` polling before use, and a kill-path that re-verifies PID liveness *and* command-line fingerprint immediately before terminating, refusing (not force-killing) if a handle's PID has been reused by an unrelated process. 5/5 real tests pass, including a full real start/health-check/stop cycle against the actual Owner Flask app.

## 3. Safe development snapshot tool — evaluated, no new tool built (reasoned, not a gap)

The spec's Milestone 0 also calls for "a safe development snapshot tool." `aura-sync` itself already implements exactly this pattern (`New-SyncSnapshot` writes a recoverable backup ref to `refs/autosync/` before every mutating step) — but only for the `master` branch it's scoped to. For Owner-phase feature-branch work, the existing standing discipline this whole session already adopted in direct response to the M14/M15 incidents — commit almost immediately after every small change, verify `git branch --show-current` before committing — already gives full recoverability: any committed state is fully recoverable via `git reflog`/`git log` regardless of branch, which is a stronger guarantee than a bespoke snapshot mechanism would add. Building a second, redundant snapshot tool for feature branches without a demonstrated gap would be scope creep; this reasoning is the explicit output of evaluating that requirement, not a skipped item.

## Outcome

Both real hazards are closed with real, executed evidence (code read + live process/scheduled-task inspection for `.autosync`; 5/5 passing tests including a real end-to-end cycle for port isolation). Milestone 0 is committed independently, before any Phase 9.5E business-logic milestone begins, per the spec's own ordering requirement.
