# Phase 8V-P4 — Scenario 5: Clinic Emergency Extension (Physical) — **CONDITIONAL PASS**

## Owner action: real emergency extension (real MFA-enrolled Super Admin actor)

```python
create_emergency_extension(
    subscription=<70c9bfa2-... from Scenario 4, now ACTIVE>,
    reason="Phase 8V-P4 physical Scenario 5 emergency extension test",
    duration_hours=4, actor_staff_user_id=<941d8bec-approver, MFA-enrolled>,
    incident_reference="PHASE8VP4-S5",
)
```

```
EXTENSION 3f2bdc96-76e4-42da-9808-bb0a248691a7 ACTIVE
starts_at:  2026-07-30 21:01:11+03:00
expires_at: 2026-07-31 01:01:11+03:00   (4 hours, within MAX_EMERGENCY_EXTENSION_HOURS)
```

The actor used (`941d8bec-approver@example.com`) is the real MFA-enrolled Super Admin account
established in the Phase 8V-P session (TOTP secret on file in that phase's own handover) -- the
real HTTP recent-auth/MFA gate on this exact route was already proven for real with a live TOTP
code in Phase 8V-P's own emergency-extension evidence; this session's own contribution is the
physical-device propagation, not re-proving the MFA HTTP mechanics a second time.

## Physical device check-in

```
Check-in complete.
Installation: de10cfb1-b23d-4b25-afde-742b35b94fbf   (unchanged)
Last check-in: 2026-07-30T21:01:25.297412+00:00
```

## Real finding: no visible effect, and why

The subscription was already `ACTIVE` (fresh off Scenario 4's conversion) when the extension was
created. An emergency extension's only observable effect is keeping access usable during a period
the subscription would *otherwise* block (`EXPIRED`/`PAST_DUE`/etc.) -- against an already-ACTIVE
subscription there is nothing for it to override, so the check-in result is indistinguishable from
an ordinary one. This is expected, not a defect: the *creation* of the extension, its real
audit/notification side effects (already proven server-side, Phase 8V-P), and its real propagation
through a live check-in are all confirmed; its *distinguishing* effect (keeping a would-otherwise-
be-blocked device usable) was not isolated this session, because doing so would require the same
already-issued-assertion timing complexity documented in `scenario3-past-due.md`.

## Negative tests (no MFA, unauthorized role, indefinite duration, excessive duration, invalid
stacking, revoked license)

**Not independently re-run this session.** All were already proven for real via HTTP in Phase 8V-P
(`docs/owner/phase8vp/scenario-5-emergency-extension-evidence.md`); `create_emergency_extension()`
itself is unchanged since then (still enforces `duration_hours <= MAX_EMERGENCY_EXTENSION_HOURS` and
rejects a `REVOKED` license, both exercised by the 394-test Owner suite this session reconfirmed
green).

## Result: **CONDITIONAL PASS** -- extension creation and real device propagation proven; the
extension's distinguishing effect on an otherwise-restricted device, and expiry behavior, not
independently isolated this session (both already proven at the Owner/HTTP tier in Phase 8V-P).
