# Customer Authentication Gap Analysis (M7.5)

Audits real customer-facing auth vs. Owner employee (staff) auth.
**No missing authority is implemented in M7** — this document
classifies what exists today only.

## Real staff auth (exists, fully implemented)

- **Model**: `StaffUser` (`app/models/staff.py:46-70`) — `email`
  (unique), `password_hash`, `is_active`, `is_super_admin`,
  `mfa_required`, `session_version`, `must_change_password`,
  `disabled_at`/`disabled_reason`, `last_login_at`, `locale`.
- **Password hashing**: Argon2id (`argon2-cffi`), min 12 chars, ≥3 of
  {lower, upper, digit, symbol} (`app/security/passwords.py:10,14,16,
  23-44`).
- **Session**: server-side `StaffSession` table
  (`app/models/staff.py:98-120`), opaque `secrets.token_urlsafe(32)`
  bearer cookie `owner_session`, only its SHA-256 hash stored
  (`app/security/tokens.py:12-18`); idle timeout 1800s, absolute
  lifetime 28800s (`app/config.py:37-38`); `session_version` bump
  invalidates all sessions on role/disable change.
- **MFA**: TOTP (`pyotp`), encrypted-at-rest secret (Fernet, keyed off
  `OWNER_SECRET_KEY`), 10 one-time recovery codes stored as SHA-256
  hashes (`app/security/mfa.py`).
- **Recent-auth step-up**: `has_recent_auth()`
  (`app/auth/session.py:130-135`) — true only within 600s of the last
  MFA verification, enforced by `require_recent_auth` on
  license-issue/transition/replace routes
  (`app/security/rbac.py:88-102`).
- **RBAC**: SQL-backed `Role`/`Permission`/`RolePermission`/
  `StaffRoleAssignment` (`app/models/staff.py:14-44`); super-admin
  bypasses role lookup entirely (`app/security/rbac.py:15-27`).

## Real customer-facing auth (confirmed absent)

**Classification: MISSING** — not `MODEL_ONLY`, not
`PARTIALLY_IMPLEMENTED`; a genuine, total absence.

Evidence for the negative conclusion (each independently sufficient):

1. `Customer` (`app/models/customers.py:14-49`) has no
   `password_hash`, `email`, `email_verified_at`, session, or token
   column. Its only email-shaped data is `CustomerContact.business_
   email` (`customers.py:59`) — plain contact-book data, no
   verification flag, never used for auth.
2. `app/customers/routes.py` — every route requires
   `load_current_staff()` (staff session); zero login/password/
   session/portal logic in the file (confirmed by grep).
3. `app/auth/` contains exactly one blueprint
   (`bp = Blueprint("auth", __name__, url_prefix="/auth")`,
   `app/auth/routes.py:38`), and every route/model it touches
   (`StaffUser`, `StaffSession`, `MfaCredential`, `StaffInvitation`)
   is staff-only — including the invitation-acceptance flow, which
   provisions a `StaffUser`, never a `Customer`
   (`app/auth/routes.py:398`,
   `from app.staff.services import create_staff_from_invitation`).
4. `app/api_external/routes.py` (the real, versioned device
   activation API) authenticates by Ed25519 device-key signature +
   license-key HMAC — a *device* proves possession of a license key
   and a device key, not a *customer* logging in with a password.
   There is no customer identity in this flow at all; a License
   belongs to a Customer only via the server-side FK, never asserted
   by the calling device.
5. `app/api/routes.py` (the separate, explicitly prototype-only,
   disabled-by-default `/api/v1/*` surface) has no login endpoint and
   its own docstring states "no authentication/signature scheme is
   wired yet" (`api/routes.py:1-6`).
6. Case-insensitive grep for `customer` across all 91 files under
   `owner/app/` returns only CRM/CRUD/FK references — none involve a
   login, password, or session concept.

## Classification per M7 checkpoint's required scheme

| Requirement | Classification | Evidence |
|---|---|---|
| Staff/employee login | `IMPLEMENTED_AND_TESTED` | `StaffUser`/`StaffSession`, `app/auth/`, extensive `owner/tests/` coverage across auth/RBAC/MFA test files |
| Staff RBAC/permission enforcement | `IMPLEMENTED_AND_TESTED` | `app/security/rbac.py`, applied on every sensitive license/installation route |
| Customer-facing login/portal | `MISSING` | No model, no route, no blueprint, no test — see six-point evidence above |
| Device-level authentication (activation/check-in/deactivation) | `IMPLEMENTED_AND_TESTED` | Ed25519 device-key signatures, `licensing_service/device_identity.py`, `owner/tests/test_phase6_activation_protocol.py` |
| Customer-owns-License authorization check at the device layer | `NOT_APPLICABLE_WITH_EVIDENCE` | The device protocol never asserts a customer identity; ownership is enforced purely by "does this device hold a valid signed key + a genuine license key", not by a logged-in customer session. There is nothing to classify as present-or-missing here because the design does not call for a customer principal at all — device-to-license binding, not customer-to-license session binding, is the real security boundary. |

## Implication for the mobile contract

A future mobile client's "authorization" is composed of two, never-
merged concepts (formalized in
`local-authorization-vs-license-entitlement.md`): (a) local Retail
app-user authorization (Retail's own existing employee/PIN model,
unrelated to Owner), and (b) commercial entitlement, proven purely at
the *device* level (Ed25519 key + license key), never at a *customer*
login level, because no such login level exists in Owner today. Any
future design that assumes a customer can "log into Owner from the
mobile app" would require new `OWNER_SERVER` work — correctly out of
scope for M7 and flagged in `licensing-gap-ownership-matrix.md`.
