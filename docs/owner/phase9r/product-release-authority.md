# Phase 9R — M10: Product Release Authority (Real New Code)

## Confirmed genuine gap, built on existing foundation

Unlike M3/M8/M9 (audit-only — existing Phase 6/7 infrastructure already
satisfied the requirements), M10 was a real gap: only an internal,
staff-only read view (`app/releases/routes.py`, `catalog.view`-gated)
existed. No publish/withdraw lifecycle, no immutability, no actor
tracking. Built on the existing `ProductVersion` model (`app/models/catalog.py`,
already had product/platform/channel/version/checksum/notes) rather than a
parallel new table — extending real prior work, consistent with this
phase's own reconciliation principle.

## What was added

**Model** (`ProductVersion`, migration `96429a63cb29`): `build_number`,
`min_supported_version`, `forced_upgrade_threshold`, `artifact_size_bytes`,
`publication_state` (`DRAFT`/`PUBLISHED`/`WITHDRAWN`, default `DRAFT`),
`created_by_staff_user_id`, `published_by_staff_user_id`, `published_at`,
`withdrawn_at`, `withdrawn_reason`.

**Service** (`app/releases/authority.py`): `publish_release()` and
`withdraw_release()`.

- `publish_release()`: only from `DRAFT`; requires a checksum and an
  artifact path already recorded; validates `forced_upgrade_threshold`
  isn't set below `min_supported_version` (lexicographic check — not a
  real semver parser, catches the obvious case, documented as such rather
  than overclaiming full semver correctness). Records actor + timestamp.
  Audited (`RELEASE_PUBLISHED`).
- `withdraw_release()`: only from `PUBLISHED`; requires a reason. Does
  **not** retroactively affect installations already running the
  withdrawn version, or leases already issued under it (M9) — it blocks
  *new* downloads/activations from referencing it. Audited
  (`RELEASE_WITHDRAWN`).

**Publication is separate from upload, for real:**
`app.catalog.services.import_release_manifest()` (Phase 7/8, pre-existing)
now explicitly creates rows in `DRAFT` state — confirmed by a real test
(`test_import_release_manifest_never_auto_publishes`) that import alone
never makes a release live. A human (or the CI pipeline, M16) must call
`publish_release()` explicitly.

**Immutability, as "versioned" not "frozen row":** once `PUBLISHED`, no
code path in this phase edits a release's version/checksum/artifact
fields — a correction is a new `ProductVersion` row (a new semver), never
an in-place edit. `publish_release()`/`withdraw_release()` only ever
mutate the lifecycle fields (`publication_state`, actor, timestamps), never
the identifying/artifact fields.

## Real test evidence

`owner/tests/test_phase9r_release_authority.py`, 9/9 passing: draft→publish
transition, publish rejected from any non-DRAFT state, publish rejected
without checksum/artifact path, forced-upgrade-below-min-supported
rejected, withdraw rejected from any non-PUBLISHED state, withdraw
requires a reason, withdraw succeeds and records reason/timestamp, and
import-never-auto-publishes. Broader regression sample (catalog + Phase
7 keyset manifest, 28 tests) re-run clean given the shared model change.

## What's next (M11)

`publication_state == "PUBLISHED"` is the gate M11's `authorize_download()`
checks before issuing any download authorization — this milestone is the
prerequisite M11 builds directly on.

## Amendment (found at closure, see `deployment-pipeline-and-migrations.md`)

Migration `96429a63cb29`'s `downgrade()` was broken
(`drop_constraint(None, ...)`) — never actually exercised until the
repository-controlled closure's final regression. Fixed there with real
Postgres-generated constraint names, proven by actually running the
downgrade and upgrading back, not just re-reading the diff. The 9 tests
above test the *service* logic (`publish_release`/`withdraw_release`), not
the migration's downgrade path — a real gap in this milestone's own
original test coverage, closed at final regression rather than here.
