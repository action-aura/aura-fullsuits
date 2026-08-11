# Phase 9.5C — Baseline

## Entry gate verification (real, executed)

```
git status --short                          -> clean at R3 tag commit
git branch --show-current (pre-switch)       -> phase9.5/owner-i18n-final-verification
git rev-parse HEAD (pre-switch)              -> 8350e9267d9bbe8e8914eb556f8501dea11d3946
git rev-list -n 1 aura-owner-i18n-rtl-verification-phase9-5b-r3-complete
                                              -> 8350e9267d9bbe8e8914eb556f8501dea11d3946
```

HEAD and the resolved R3 tag are **identical** — confirms the R3 tag
resolves to the exact final commit of the prior wave (the 5-commit
sequence: flake fix, service-error architecture, catalog authorship,
docs, README pointer).

## Historical tags — resolved and confirmed unmoved

| Tag | Resolved commit | Matches spec-provided hash |
|---|---|---|
| `aura-commercial-licensing-operations-phase8-complete` | `4131e610e8e554193b01c1e923ede00f374100e3` | `4131e61` — match |
| `aura-owner-commercial-operations-phase9-5a-complete` | `21be07bf5b24a3a26925e2972153f5aea9377212` | `21be07b` — match |
| `aura-owner-employee-management-portal-phase9-5b-complete` | `61e507ebcf2fbaf027063d40dd42d3398840f825` | `61e507e` — match |
| `aura-owner-i18n-rtl-foundation-phase9-5b-r-complete` | `632100dd3d2d989f10497223e97677646d65be7a` | `632100d` — match |
| `aura-owner-i18n-rtl-final-closure-phase9-5b-r2-complete` | `ba69736c14670759ef0e40a92b60fa4e0289d7ce` | `ba69736` — match |
| `aura-owner-i18n-rtl-verification-phase9-5b-r3-complete` | `8350e9267d9bbe8e8914eb556f8501dea11d3946` | (this wave's own entry point) |

`aura-secure-staging-phase9-complete` — `git tag --list` returns empty.
Not created, as required.

## Branch created

```
git switch -c phase9.5/leads-customers-followups-location \
  aura-owner-i18n-rtl-verification-phase9-5b-r3-complete
```

Confirmed: branch HEAD = `8350e9267d9bbe8e8914eb556f8501dea11d3946`, the
exact R3 tag commit — not an assumed hash.

## Legacy repository read-only re-check

```
git status --short | wc -l                          -> 37
git rev-parse HEAD                                    -> 414e6ea5ca9fc698fe075a4ae4464cebf0b7bf34
git diff --stat (tail)                                 -> 22 files changed, 1441 insertions(+), 260 deletions(-)
git status --short | grep "^??" | wc -l               -> 15
```

**Byte-identical** to the state recorded at Phase 9.5B-R3's entry and
final checks. Zero mutation across the entire prior wave and this wave's
entry. Only read-only commands (`status`, `rev-parse`, `diff --stat`)
were run against this repository.

Notably, the legacy repository's *pre-existing, uncommitted* working
tree already contains a substantial `core/crm/` package (domain.py, db.py,
dto/, services/lead_*.py, repository/lead_*.py) — this is prior-art
reference material only. It is read-only and was not copied, ported, or
otherwise used as source; Phase 9.5C's actual implementation reuses the
**already-migrated, already-tested** `aura-fullsuits` foundation from
Phase 9.5A instead (see `existing-crm-foundation-audit.md`).

## Required reading — completed

All 9 `docs/owner/phase9_5b_r3/*` documents and the 10 `docs/owner/
phase9_5a/*` documents named in the governing spec were read. Key
finding carried forward: Phase 9.5A already implemented a real Lead/
Customer/Location domain layer (models, migrations, ownership filter,
duplicate detection, conversion service, location-capture service) that
was never wired to any route, API, or UI — Phase 9.5C's job is
overwhelmingly **extend and wire**, not build from scratch. Full
classification in `existing-crm-foundation-audit.md`.
