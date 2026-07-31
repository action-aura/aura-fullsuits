# Phase 8V-P7 — Retained Evidence Validity

## Remains valid, no smoke check needed (no source change affecting it, no artifact rebuild affects it)

- Scenario 1 (Clinic early renewal), Scenario 4 (Clinic pilot conversion): retained from Phase 8V-P4/P5,
  no source change in this or the prior session touches renewal or pilot-conversion logic.
- Installation-ID/device-key/device-slot continuity, assertion refresh, no license-key retransmission:
  structural properties re-confirmed as an unavoidable side effect of every check-in this session
  performs (see Part I evidence).
- Owner preflight `ok: true`: re-run fresh this session (`final-validation-environment.md`), not merely
  assumed.

## Requires a smoke check this session (artifact rebuilt, need to confirm the SAME behavior survives
the rebuild -- per this session's own Part H instruction)

- Scenario 1, Scenario 3, Scenario 4, Scenario 5 -- all physically smoke-re-confirmed against the final
  rebuilt Clinic artifact (Retail Scenario-equivalent covered fresh in Part I, not merely smoke-checked,
  since Retail's artifact never had physical confirmation of the fix at all before this session).

## Invalidated by a rebuilt artifact

None -- the fix itself was already proven on a rebuilt Clinic artifact in Phase 8V-P6; this session's
Clinic rebuild (if source is unchanged since) is expected to be byte-for-byte reproducible modulo
version bump, not a behavior change.

## Requires full execution this session (never completed)

Scenario 2 (Retail), Scenario 6, Scenario 7, stale-assertion rejection, Clinic+Retail backup/restore/
export, Clinic invoice/payment, full raw wire + Logcat privacy review across all these scenarios,
on-device data preservation baseline comparison, final artifact/manifest audit for Retail/Windows
artifacts (never produced before).
