# External Workspace Exit Fingerprints (M10 Exit)

Real, executed fingerprints at M10 close, same 8-command protocol as
`external-workspace-entry-fingerprints-m10.md`: `HEAD`, modified count,
untracked count, `git diff | sha256sum` (DIFF_BINARY_HASH), `git diff
--cached | sha256sum` (DIFF_CACHED_BINARY_HASH), and `git status
--porcelain | grep '^??' | sort | sha256sum` (UNTRACKED_LIST_HASH,
directory-collapsed, matching the exact established M9/M10-entry
protocol — a first attempt using `git ls-files --others` instead
produced a mismatching hash purely because it expands untracked
directories into every contained file; re-verified against the
directory-collapsed `git status --porcelain` method and confirmed
byte-identical for the legacy repo, see below).

Real, required guarantee re-confirmed: `NO_M10_ATTRIBUTABLE_EXTERNAL_
WORKSPACE_CHANGE` — every command this milestone ran against these
three paths was read-only (`git status`/`git diff`/`git rev-parse`,
captured here and at entry); no write command was ever issued against
any of them in this session.

## `C:\Users\Dell\Desktop\AuraEnterprise\aura-fullsuits-phase9r`

```
HEAD: 8c35768393d9407f72ef34aa93705f9c454453f7   (unchanged since M6-M10 entry)
STATUS / DIFF_BINARY_HASH / DIFF_CACHED_BINARY_HASH / UNTRACKED_LIST_HASH:
  e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85 (empty, all)
```

**Byte-identical to M10 entry.** `PHASE9R_WORKTREE_BYTE_IDENTICAL = TRUE`.

## `C:\Users\Dell\Desktop\AuraEnterprise\AuraEnterprise` (legacy repo)

```
HEAD: 414e6ea5ca9fc698fe075a4ae4464cebf0b7bf34   (unchanged since M6-M10 entry)
STATUS: 22 modified, 15 untracked (byte-identical to M10 entry)
DIFF_BINARY_HASH:        cc37af7f7c95450e59be66bb4785a67bdfaeae18f7c86bbc2e3161ab76c66f7
DIFF_CACHED_BINARY_HASH: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85
UNTRACKED_LIST_HASH:     cafdb44a28f4865453bf28d1def21c1ca34496052ed638da5882a776bb4afe9
```

**Byte-identical to M10 entry.** `LEGACY_REPO_BYTE_IDENTICAL = TRUE`.
Never touched by this milestone (never `cd`'d into, never written to
in this session — read-only `git status`/`git diff` only).

## `C:\Users\Dell\Desktop\AuraEnterprise\aura-fullsuits-owner-ui` (Owner UI-modernization worktree)

```
HEAD: f59b021922a6559183e3a829f74594e17ab79e41   (unchanged since M9/M10 entry -- no new commits)
Branch: feat/owner-ui-ux-modernization
STATUS: 17 modified, 7 untracked (M10 entry: 0 modified, 1 untracked)
DIFF_BINARY_HASH:        aa78461b6a9ab0a4e5b578e79f4587e6e646dbf5f9671e712f13414076e6e08
DIFF_CACHED_BINARY_HASH: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85 (empty -- nothing staged)
UNTRACKED_LIST_HASH:     fb1ed750d822c57d2206b493401503f1d184694f1c9e66aff59a65365153f28
```

**`OWNER_UI_WORKTREE_BYTE_IDENTICAL = FALSE`.**
**`M10_CONTRIBUTION_TO_OWNER_UI_WORKTREE = ZERO`.**
**`CONCURRENT_CHANGE_ATTRIBUTED = TRUE`.**

This worktree changed again during the M10 window — real, investigated,
and fully attributed here, exactly as the checkpoint's own instruction
requires ("do not rewrite this as 'all external workspaces were
unchanged'"). Real, observed content of the new uncommitted working-tree
changes (`HEAD` itself did not move -- these are real, in-progress,
not-yet-committed edits on the same `feat/owner-ui-ux-modernization`
branch):

- Modified: `owner/app/__init__.py`, `owner/app/auth/routes.py`,
  `owner/app/security/rbac.py`, `owner/app/static/css/components.css`,
  six `owner/app/templates/auth/*.html` files (accept-invitation,
  change-password, login, MFA enroll/recovery/verify, reauth),
  `owner/app/templates/dashboard/index.html`,
  `owner/app/templates/layout/base.html`, three
  `owner/tests/test_phase9_5b_r*.py` files, `requirements/owner-server.txt`.
- Untracked (new): `docs/owner/ui-modernization/first-login-welcome-contract.md`,
  `owner/app/errors.py`, `owner/app/static/js/password-toggle.js`,
  `owner/app/static/js/product-tour.js`, `owner/app/templates/errors/`,
  `owner/app/templates/layout/_tour.html`, plus the already-present `var/`.

Real classification: this is independent, active work on Owner UI/UX
modernization — file names and the referenced `phase9_5b_r`/`r2` test
files indicate a real, separate, in-progress initiative (first-login
welcome flow, product tour, password-visibility toggle, MFA/auth
template polish, RBAC change) — not anything M10's own commands could
have produced (M10 never wrote to Mobile-unrelated Owner
auth/RBAC/template files, and no command in this session's own history
targeted this path for anything other than the read-only fingerprint
capture itself). No M10 commit, test run, or build command in this
session ever executed with this worktree as the working directory.

## Real, honest overall M10 exit classification

Two of three external workspaces (`aura-fullsuits-phase9r`, legacy
`AuraEnterprise`) are byte-identical to M10 entry. The third
(`aura-fullsuits-owner-ui`) changed for its own, independently-active,
real, unrelated reasons — investigated and attributed above, not
silently claimed unchanged and not silently omitted. `NO_M10_
ATTRIBUTABLE_EXTERNAL_WORKSPACE_CHANGE` holds: M10 itself contributed
zero bytes to any of the three.
