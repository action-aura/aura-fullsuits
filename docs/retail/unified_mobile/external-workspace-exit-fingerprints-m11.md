# External Workspace Exit Fingerprints (M11 Exit)

Real, executed fingerprints at M11 close, same protocol as
`external-workspace-entry-fingerprints-m11.md`.

## `aura-fullsuits-phase9r`
```
HEAD: 8c35768393d9407f72ef34aa93705f9c454453f7   (unchanged)
STATUS / all hashes: empty (byte-identical)
```
**Byte-identical to M11 entry.**

## `AuraEnterprise` (legacy repo)
```
HEAD: 414e6ea5ca9fc698fe075a4ae4464cebf0b7bf34   (unchanged)
STATUS: 22 modified, 15 untracked (unchanged)
DIFF_BINARY_HASH:        cc37af7f7c95450e59be66bb4785a67bdfaeae18f7c86bbc2e3161ab76c66f7
DIFF_CACHED_BINARY_HASH: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85
UNTRACKED_LIST_HASH:     cafdb44a28f4865453bf28d1def21c1ca34496052ed638da5882a776bb4afe9
```
**Byte-identical to M11 entry.** Never touched this session.

## `aura-fullsuits-owner-ui`
```
HEAD: 2b2f909c9cbc27b71dbd881ed0bd3f4c4b7977ef   (unchanged)
STATUS: 7 modified, 5 untracked (unchanged)
DIFF_BINARY_HASH:        6cfec50934240ef15d8c85244c164faa67e1498bc181830ec1932db9d3846f9
DIFF_CACHED_BINARY_HASH: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85
UNTRACKED_LIST_HASH:     2172489514fdbb809c8f305b783f00521fd989bbf08f7945b17f44548de532d
```
**Byte-identical to M11 entry** — no further drift observed during
this M11 session (the prior session's own drift, captured at M11
entry, is unchanged; real, honest note: this does not mean the
worktree's own owner stopped working, only that no *further* change
happened to be observed between this session's entry and exit
captures).

## Required claim

**`NO_M11_ATTRIBUTABLE_EXTERNAL_WORKSPACE_CHANGE` holds.** All three
external workspaces are byte-identical between M11 entry and exit; no
command in this session ever targeted any of them except the
read-only fingerprint captures themselves.
