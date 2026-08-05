# License State Machine Contract (M7.3)

Exact real License lifecycle, read directly from
`owner/app/models/licensing.py` and `owner/app/licensing/services.py`
— not assumed from any other product's naming convention.

## Real states

`License.status` is `String(32)` (`app/models/licensing.py:32`), not a
DB enum, default `"DRAFT"`. The seven real, observed states are:

```
DRAFT, ISSUED, ACTIVE, SUSPENDED, EXPIRED, REVOKED, REPLACED
```

There is no `ARCHIVED` state — `REPLACED` fills that terminal role for
a superseded license.

## Real transition table

`VALID_TRANSITIONS`, `app/licensing/services.py:15-23`:

| From | Allowed to |
|---|---|
| `DRAFT` | `ISSUED` |
| `ISSUED` | `ACTIVE`, `SUSPENDED`, `REVOKED` |
| `ACTIVE` | `SUSPENDED`, `EXPIRED`, `REVOKED` |
| `SUSPENDED` | `ACTIVE`, `REVOKED`, `EXPIRED` |
| `EXPIRED` | `REPLACED` |
| `REVOKED` | (terminal) |
| `REPLACED` | (terminal) |

Enforced generically by `transition_license()`
(`app/licensing/services.py:104-128`, gated by `VALID_TRANSITIONS` at
lines 105-107).

## Real transition entry points

- **`DRAFT` created**: `create_license()` (`services.py:30-42`).
- **`DRAFT → ISSUED`**: `issue_license_key()` (`services.py:57-65`),
  route `POST /licenses/<id>/issue` (`routes.py:81-98`), requires
  `require_recent_auth`. This is the one, single point where the
  plaintext key is generated and returned — never again.
- **Generic transitions** (`ISSUED/ACTIVE/SUSPENDED → …`):
  `transition_license()`, route `POST /licenses/<id>/transition`
  (`routes.py:101-123`). The route maps the target status to a
  distinct RBAC permission: `licenses.revoke` (→`REVOKED`),
  `licenses.suspend` (→`SUSPENDED`), `licenses.reactivate`
  (→`ACTIVE`) — reactivation requires a **separate** permission from
  suspension, a real, deliberate separation of duty.
- **`EXPIRED`/other → `REVOKED`/`REPLACED` (via replacement)**:
  `replace_license()` (`services.py:131-160`), route
  `POST /licenses/<id>/replace`. Only callable when the old license is
  `ACTIVE`/`ISSUED`/`SUSPENDED`/`EXPIRED`. New license created in
  `DRAFT`; old license transitioned to `REPLACED` if it was `EXPIRED`,
  else `REVOKED`.
- **No automatic `→ EXPIRED` transition exists** in `app/licensing/`
  or `app/licensing_service/` — confirmed by grep across both
  directories for `"EXPIRED"` assignments; the only automatic,
  date-driven expiry job in the codebase operates on
  `Subscription.status`, not `License.status`
  (`app/commercial_ops/expiry_scan.py`). A License reaching `EXPIRED`
  is currently a manual staff action.

## Real read-only gating (does not mutate `status`)

- **Activation** (`app/licensing_service/activation.py:130-141`):
  rejects `DRAFT` (`LICENSE_NOT_ISSUED`), `SUSPENDED`
  (`LICENSE_SUSPENDED`), `REVOKED` (`LICENSE_REVOKED`), `REPLACED`
  (`LICENSE_REPLACED`), `EXPIRED` (`LICENSE_EXPIRED`); only
  `ISSUED`/`ACTIVE` proceed.
- **Check-in** (`app/licensing_service/checkin.py:92-98`): rejects
  `SUSPENDED`, `REVOKED`, `EXPIRED`.

## No cascade to Installations

Neither `transition_license()` nor `replace_license()` touches any
`Installation` row (confirmed by reading both function bodies in
full). A revoked/suspended License only affects its Installations
indirectly, the next time each Installation attempts activation or
check-in. This is a real, current behavior — recorded as fact for the
mobile contract to design around, not something M7 changes.

## Real test coverage

`owner/tests/test_licensing.py:108` —
`test_invalid_status_transition_rejected`, directly exercising
`VALID_TRANSITIONS`. `owner/tests/test_phase6_activation_protocol.py`
covers every activation-time rejection reason above end-to-end (real
HTTP, real signatures).

## Mobile contract implication

The shared `LicenseStatus` model (M7.17) must enumerate exactly these
seven real values — `DRAFT, ISSUED, ACTIVE, SUSPENDED, EXPIRED,
REVOKED, REPLACED` — with no invented `ARCHIVED`/`PENDING`/`TRIAL`
value. M7.18's fixture suite must include one fixture per state
reachable via activation/check-in (i.e. `ISSUED`, `ACTIVE`,
`SUSPENDED`, `EXPIRED`, `REVOKED`, `REPLACED` — `DRAFT` is never
reachable by a device since it precedes key issuance).
