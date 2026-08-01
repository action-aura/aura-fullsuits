# Phase 9.5C — Milestone 2: Lead Lifecycle Contract

## Canonical vocabulary (unchanged, reused verbatim)

`app/models/leads.py:22-25` already defines the real stored values —
matches the governing spec's suggested vocabulary exactly, nothing
renamed:

```
NEW, NOT_INTERESTED_NOW, POTENTIAL, FOLLOW_UP, UNDER_OBSERVATION,
QUALIFIED, CONFIRMED, LOST, ARCHIVED
```

## Transition matrix

| From | To | Permission | Ownership required | Reason required | Notes |
|---|---|---|---|---|---|
| `NEW` | `POTENTIAL` | `leads.update_own`/`update_all` | yes (unless `_all`) | no | |
| `NEW` | `NOT_INTERESTED_NOW` | same | yes | no | |
| `POTENTIAL` | `FOLLOW_UP` | same | yes | no | |
| `POTENTIAL` | `NOT_INTERESTED_NOW` | same | yes | no | |
| `FOLLOW_UP` | `UNDER_OBSERVATION` | same | yes | no | |
| `FOLLOW_UP` | `NOT_INTERESTED_NOW` | same | yes | no | |
| `UNDER_OBSERVATION` | `QUALIFIED` | same | yes | no | |
| `UNDER_OBSERVATION` | `NOT_INTERESTED_NOW` | same | yes | no | |
| `QUALIFIED` | `CONFIRMED` | `leads.convert` (separate permission) | yes | no | Only reachable through the conversion service (Milestone 13), never a bare status write — `CONFIRMED` is not a directly-settable target of `change_lead_status()`. |
| any of `NEW/POTENTIAL/FOLLOW_UP/UNDER_OBSERVATION/QUALIFIED` | `LOST` | `leads.update_own`/`update_all` | yes | **yes** | "any active state -> LOST" per spec. |
| any of `NEW/POTENTIAL/FOLLOW_UP/UNDER_OBSERVATION/QUALIFIED/NOT_INTERESTED_NOW/LOST/CONFIRMED` | `ARCHIVED` | `leads.archive` | yes | no | Terminal-only: only reachable from a state that is itself terminal or explicitly archivable (`NOT_INTERESTED_NOW`, `LOST`, `CONFIRMED`) — an active pipeline stage must go through `LOST`/`NOT_INTERESTED_NOW`/`CONFIRMED` first, never skip straight to `ARCHIVED` (prevents silently hiding an in-flight Lead). |

All other transitions (including any transition FROM `CONFIRMED` or
`ARCHIVED`, and any transition that is not explicitly listed above) are
**rejected** with `StableCodeError("INVALID_LEAD_TRANSITION",
from_status=..., to_status=...)`.

## Rules (implemented in `change_lead_status()`)

- An employee cannot directly mutate status outside the service — there
  is no route that writes `Lead.status` except through
  `change_lead_status()`.
- An employee cannot transition another employee's Lead — enforced at
  the route layer via `apply_ownership_filter`-equivalent single-record
  ownership check (creator or current assignee) unless the actor holds
  `leads.update_all`.
- A converted Lead (`CONFIRMED`) cannot be converted again — enforced
  both here (no transition out of `CONFIRMED` via this function) and in
  `conversion.py` (`InvalidLeadStateError` already guards `LOST`/
  `ARCHIVED`; this milestone additionally guards re-converting a
  `CONFIRMED` lead, since `convert()` currently doesn't check for that —
  see `lead-transition-matrix.md`'s explicit note).
- Archival does not erase history — `ARCHIVED` only sets `archived_at`
  and appends `LeadStatusHistory`; no row is deleted (Non-Negotiable
  Domain Rule 12).
- Status history is append-only — unchanged, already correct.
- Unknown status values render safely through localized labels — the
  presentation-layer label lookup (Milestone 18) falls back to the raw
  code string for any value not in its label dict, never raises.
- English and Arabic labels added in the same commit that introduces the
  transition guard (Milestone 18), not postponed.

## Optimistic-lock and idempotency behavior

- `change_lead_status()` now accepts an `expected_version: int` and
  raises `StableCodeError("STALE_LEAD_VERSION")` if
  `lead.version != expected_version`, mirroring the pattern already used
  elsewhere in Owner (`commercial_ops` renewal/pilot services).
- Status transitions are not idempotency-keyed (unlike conversion) —
  repeating an already-applied transition (e.g. `POTENTIAL -> POTENTIAL`)
  is rejected as an invalid transition (self-transitions are not in the
  matrix), which is itself a safe, side-effect-free rejection.

See `lead-transition-matrix.md` for the exact code-level transition
table used by the implementation.
