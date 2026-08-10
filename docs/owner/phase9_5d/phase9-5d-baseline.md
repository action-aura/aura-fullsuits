# Phase 9.5D — Baseline

## Repositories

- Primary: `C:\Users\Dell\Desktop\AuraEnterprise\aura-fullsuits` — all work happens here.
- Legacy: `C:\Users\Dell\Desktop\AuraEnterprise\AuraEnterprise` — strictly read-only. Confirmed at entry: HEAD `414e6ea5ca9fc698fe075a4ae4464cebf0b7bf34`, 22 modified tracked files + 15 untracked files, all pre-existing (mtimes 2026-07-11/12, weeks before this phase started) — unchanged from the state recorded at the end of Phase 9.5C. Only read-only git commands (`status --short`, `rev-parse HEAD`, `diff --stat`, `diff --name-only`, `ls-files --others --exclude-standard`) were run against it.

## Branch

Created via `git switch -c phase9.5/commercial-sales-orders-payments-commissions aura-owner-leads-customers-crm-phase9-5c-complete`, directly from the resolved tag commit (not an unverified local HEAD).

- Tag `aura-owner-leads-customers-crm-phase9-5c-complete` resolves to `34291622fe91f1f929eacdb26883f6d66f99fbb0` (short `3429162`), matching the spec's stated starting commit exactly.
- New branch HEAD after `switch -c`: identical commit (`3429162...`), confirmed.

## Historical tags verified present and unmoved

All tags below are annotated tags; `git rev-parse <tag>` returns the tag-object SHA, not the commit SHA — verification below peels to the underlying commit (`git show --no-patch --format=%H`) to match against the spec's stated commit hashes.

| Tag | Resolved commit | Verified against spec |
|---|---|---|
| `aura-commercial-licensing-operations-phase8-complete` | `4131e610e8e554193b01c1e923ede00f374100e3` | matches `4131e61` |
| `aura-owner-commercial-operations-phase9-5a-complete` | `21be07bf5b24a3a26925e2972153f5aea9377212` | matches `21be07b` |
| `aura-owner-employee-management-portal-phase9-5b-complete` | `61e507ebcf2fbaf027063d40dd42d3398840f825` | matches `61e507e` |
| `aura-owner-i18n-rtl-foundation-phase9-5b-r-complete` | `632100dd3d2d989f10497223e97677646d65be7a` | matches `632100d` |
| `aura-owner-i18n-rtl-final-closure-phase9-5b-r2-complete` | `ba69736c14670759ef0e40a92b60fa4e0289d7ce` | matches `ba69736` |
| `aura-owner-i18n-rtl-verification-phase9-5b-r3-complete` | `8350e9267d9bbe8e8914eb556f8501dea11d3946` | resolved and recorded (spec asked to "resolve and record from Git", no fixed hash given) |
| `aura-owner-leads-customers-crm-phase9-5c-complete` | `34291622fe91f1f929eacdb26883f6d66f99fbb0` | matches `3429162` |
| `aura-secure-staging-phase9-complete` | does not exist (`git tag --list` returns empty) | confirmed absent, as expected |

All 7 real historical tags verified present, correctly resolved, and untouched by this phase.

## Spec truncation, honestly recorded

The governing Phase 9.5D specification was cut off by the platform's message-length limit mid-Milestone 22 ("Do n[...]"), the same failure mode that truncated the Phase 9.5C spec mid-way through this same conversation. Milestones 23 onward (if any beyond 22), the final tag name, and the exact final acceptance/gate-matrix criteria were not received. Work proceeds through every fully-specified milestone (Entry Gate through Milestone 22 as received); the final tag will not be created until either the continuation is supplied (mirroring how Phase 9.5C's continuation was handled) or a reasonable, clearly-flagged completion point is reached and reported honestly as such.

## Full-suite regression baseline (must not regress)

From Phase 9.5C's final closure: Owner 673/673, Retail/Clinic/commercial_runtime/licensing_contracts 46/46 files. These are the numbers this phase's final regression must meet or exceed — never assumed, always re-executed.
