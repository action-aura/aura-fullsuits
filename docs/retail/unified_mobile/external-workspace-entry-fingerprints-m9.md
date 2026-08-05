# External Workspace Entry Fingerprints (M9 Entry)

Real, executed fingerprints captured at the exact start of M9. Unified
Mobile branch `feat/retail-unified-mobile-android-ios`, real full HEAD
at capture time: `cff1991d5f6683edf341a6328effb160515e6548`.

**Note on HEAD**: the M9-checkpoint's own text names `62c5309` as the
"accepted M8 HEAD." Real, current `git log` shows two additional real
commits landed after `62c5309` (`4d62e60`, `cff1991`), both docs-only,
both further evidence-tightening of the Owner/customer boundary
material already reviewed at M8 close — no code, no `owner/`, no
external workspace touched by either. Per this milestone's own
discipline (real evidence over a stated assumption), `cff1991` is
recorded as the real M9 entry point, not the checkpoint's now-stale
`62c5309` reference.

Accepted M8 baselines, carried forward as the real starting numbers
this milestone measures against:
- Shared tests: **596**
- Retail Python: **194** (not independently re-verified this session;
  same disclosed pattern as M7/M8)
- Android APK: builds (`androidApp-debug.apk`, confirmed at M8 close)

## `C:\Users\Dell\Desktop\AuraEnterprise\aura-fullsuits-phase9r`

```
HEAD: 8c35768393d9407f72ef34aa93705f9c454453f7   (unchanged since M6/M7/M8)
STATUS: (empty, 0 lines)
DIFF_BINARY_HASH / DIFF_CACHED_BINARY_HASH / UNTRACKED_LIST_HASH:
  e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85 (empty, all three)
```

## `C:\Users\Dell\Desktop\AuraEnterprise\AuraEnterprise` (legacy repo)

```
HEAD: 414e6ea5ca9fc698fe075a4ae4464cebf0b7bf34   (unchanged since M6/M7/M8)

STATUS: 22 modified, 15 untracked (byte-identical to M8 exit)

DIFFSTAT: 22 files changed, 1441 insertions(+), 260 deletions(-)

DIFF_BINARY_HASH:        cc37af7f7c95450e59be66bb4785a67bdfaeae18f7c86bbc2e3161ab76c66f7
DIFF_CACHED_BINARY_HASH: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85   (empty)
UNTRACKED_LIST_HASH:     cafdb44a28f4865453bf28d1def21c1ca34496052ed638da5882a776bb4afe9f
```

Literal sorted untracked path list (unchanged from M8 exit):

```
?? core/crm/repository/lead_conversion.py
?? core/crm/repository/lead_duplicate.py
?? core/crm/repository/lead_merge.py
?? core/crm/repository/lead_timeline.py
?? core/crm/services/lead_conversion_service.py
?? core/crm/services/lead_duplicate_service.py
?? core/crm/services/lead_management_query_service.py
?? core/crm/services/lead_merge_service.py
?? core/crm/services/lead_normalization_service.py
?? core/crm/services/lead_timeline_service.py
?? core/retail/
?? core/security/
?? docs/retail/
?? tests/retail_pricing_test.py
?? tests/retail_security_test.py
```

## `C:\Users\Dell\Desktop\AuraEnterprise\aura-fullsuits-owner-ui` (Owner UI-modernization worktree — newly discovered this milestone)

Real, first-time capture — this worktree did not exist (or was not
yet identified) at any prior milestone's own fingerprint check.

```
HEAD: 78745176eb7e58c348f5b968fbf7258dd17cbb51

STATUS: 1 modified, 9 untracked

DIFFSTAT: 1 file changed, 125 insertions(+), 137 deletions(-)   (owner/app/templates/layout/base.html)

DIFF_BINARY_HASH:        c5a4bc6657a3d19392329b426389aa95ed9a28b80dfa98db91adb00ad04a81c
DIFF_CACHED_BINARY_HASH: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85   (empty, nothing staged)
UNTRACKED_LIST_HASH:     6f5d42a0478a65518aa57216a6463d8f04298cc078ae02e74b9587b209554cf
```

Literal sorted untracked path list:

```
?? docs/owner/ui-modernization/application-shell-contract.md
?? docs/owner/ui-modernization/light-dark-theme-contract.md
?? docs/owner/ui-modernization/typography-contract.md
?? owner/app/static/css/components.css
?? owner/app/static/css/shell.css
?? owner/app/static/js/sidebar.js
?? owner/app/static/js/theme.js
?? owner/app/templates/layout/_sidebar.html
?? var/
```

Real, pre-existing, unrelated Owner UI-modernization work-in-progress
(a separate real effort against `owner/app/templates/`/`static/`, not
touched by, related to, or caused by anything in this Unified Mobile
branch). Recorded, never modified.

## What "unchanged" will mean at M9 exit

At M9 exit, the same protocol (including literal sorted untracked path
lists for all three workspaces) will be re-run. Required conclusion:
`UNCHANGED_RELATIVE_TO_M9_ENTRY_STATE` — every value above, for all
three workspaces, must match exactly. No command in this milestone
resets, cleans, stages, restores, creates, deletes, or modifies files
in any external workspace, including the newly-identified Owner
UI-modernization worktree.
