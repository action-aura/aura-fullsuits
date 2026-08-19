# Branch and release map

**Verified 2026-08-19 against `origin`.** Every number here was measured, not
assumed — the commands are given so this can be re-verified rather than
trusted. Re-check before acting on it; branches move.

This document exists because the repo reached a state where nobody could
answer "what is actually on master, and what is the latest?" without an hour
of git archaeology. That question should be answerable in one page.

## Why this was hard to see

Two Owner lineages evolved in parallel and both touched the same files. Add
squash-merges (which make a merged branch's commits look unmerged, because
the SHAs differ), and `git log --oneline master..branch` gives a wildly
misleading picture of how much work is outstanding.

Compare **tree hashes**, not commit counts:

```bash
git rev-parse origin/master^{tree}
git rev-parse origin/<branch>^{tree}
```

Equal hashes mean the content is identical no matter what the commit graph
says. That is how `fix/week2-cash-closing-self-approval` was confirmed fully
merged before its remote ref was deleted.

> **Tooling warning:** `rtk git diff` returned *empty output* for real,
> non-empty diffs during this audit. It caused a live misreading (PR #1 was
> briefly assessed as redundant when it is not). Use `git diff-tree -r
> --name-status A B` for anything you intend to act on.

## What is on `master`

Tip `ef27c15`, **536 files under `owner/`**.

- E-W0.3 CI hardening: owner tests sharded a–d, coverage baseline, security
  and supply-chain scans, staging package config check (PR #2)
- AUDIT-031 / AUDIT-032 cash-closing fixes — submitter blocked from approving
  their own closing, JSON API twin route closed (PR #3)
- `AURA_EINVOICING_DISABLED` kill-switch documentation

**`master` does NOT have:** the server-side sync engine (`owner/app/sync/`),
the department-nav / KPI-tile UI work, or the licensing P0 hardening fixes.

> The claim in `CLAUDE.md` that "master lags significantly behind these
> branches — hundreds of files' worth in `owner/` alone" is **out of date**.
> Master carries 536 `owner/` files against 543 on the UI lineage tip. The
> remaining gap is small and specific, not hundreds of files.

## Lineage 1 — Owner UI

```
origin/feat/owner-ui-ux-modernization   (PR #4)
        └── origin/feat/owner-ui-department-nav  (PR #5)  <- tip, 543 owner/ files
```

**PR #4 is an ancestor of PR #5.** Landing #5 subsumes #4 — this is one merge,
not two. Verify before acting:

```bash
git merge-base --is-ancestor origin/feat/owner-ui-ux-modernization \
                             origin/feat/owner-ui-department-nav && echo YES
```

Merging this tip into `master` produces **29 conflicting files** (measured via
a trial merge on 2026-08-19), spanning `requirements/base.txt`,
`requirements/owner-server.txt`, four layout/dashboard templates, six
`owner/tests/*` files as add/add conflicts, and the CI workflow.

## Lineage 2 — sync engine

```
origin/feat/multi-device-sync-foundation
        └── origin/feat/retail-catalog-party-sync   (481 owner/ files)
                └── origin/feat/seq-advisory-lock-fix   (B2 fix, b9af4c3)
```

This is the **only** lineage carrying `owner/app/sync/`. Confirm with:

```bash
git ls-tree -d --name-only origin/feat/retail-catalog-party-sync owner/app/sync
git ls-tree -d --name-only origin/master owner/app/sync   # empty
```

It is an *older* `owner/` snapshot (481 files) than master, so it cannot be
merged forward naively — it would regress files master already has. Its
Alembic revision graph is expected to be incompatible with lineage 1's, since
the two diverged before several migrations landed on each side.

## Blocking gates for any reconciliation

These are not suggestions. Both have already been lost or nearly lost once.

1. **B2 seq-advisory-lock fix must be explicitly re-verified.** Standing
   instruction from 2026-08-10. The `pg_advisory_xact_lock` seq-hole fix
   (`b9af4c3`) originates on a branch that may be superseded during
   reconciliation. Do not trust a 3-way merge to carry it. Confirm the fix is
   present in the result and covered by a passing test before merging.

2. **AUDIT-032 self-approval block must survive.** `owner/app/cash_closing/
   services.py` and `owner/app/models/cash_closing.py` are both in the
   29-file conflict set, and both carry the fix that stops a submitter
   approving their own cash closing. A careless "take theirs" resolution
   silently reverts a security control. The cash-closing and IDOR tests must
   pass on the merge result, not just on either side.

## Deployment

Live Owner Control Center: `161.35.219.243` / `owner.actionaura.me`
(DigitalOcean droplet named `aura-retail-demo`, fra1, 2GB/1vCPU/50GB).

- Code: `/opt/aura-owner`
- Virtualenv: `/opt/aura-owner/.venv-owner`
- Environment: `/etc/aura-owner.env`

**Required after every deploy** — neither is automatic, both have caused a
silent production failure already:

```bash
cd /opt/aura-owner/owner
set -a; . /etc/aura-owner.env; set +a
FLASK_APP='app:create_app()' /opt/aura-owner/.venv-owner/bin/flask seed-rbac
FLASK_APP='app:create_app()' /opt/aura-owner/.venv-owner/bin/flask commercial preflight
```

`seed-rbac` is idempotent and additive. Without it the `permissions` and
`roles` tables stay empty, which makes the superadmin bypass compute an empty
permission set — every permission check silently returns False and the
dashboard renders its "no overview permission" empty state.

`commercial preflight` is a genuine blocking check, not informational. It
catches the stale `trust_anchor.json` copy at
`/opt/aura-owner/commercial_runtime/licensing_contracts/trust_anchor.json`,
which does not re-sync itself after a signing-key rotation.

**There is currently no snapshot of this droplet** (`doctl compute snapshot
list` is empty). Take one before any deploy that touches migrations.

## Branch hygiene going forward

- **Push the same day you commit.** On 2026-08-19, 8 commits existed only on
  this laptop — including three licensing P0 security fixes and the B2 fix.
  A disk failure would have lost them. `git log --branches --not --remotes`
  lists anything at risk; it should normally print nothing.
- **Delete a remote branch after a squash-merge only once its tree hash
  equals master's.** Commit counts lie after a squash; tree hashes do not.
- **One lineage at a time.** Both current lineages touch the same Owner files.
  Landing them independently is what produced the 29-file conflict set.
- **Check `docs/ops/` before editing shared root files** (`requirements/*`,
  `.gitignore`, CI workflow). Both current lineages conflict on exactly these.
