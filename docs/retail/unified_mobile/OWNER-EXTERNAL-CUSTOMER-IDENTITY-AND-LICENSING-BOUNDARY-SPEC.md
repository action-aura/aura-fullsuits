# Owner External Customer Identity and Licensing Boundary Specification

**Renamed from `OWNER-CUSTOMER-AUTH-PREREQUISITE-SPEC.md`** (see
`owner-internal-vs-customer-boundary.md`) — the original name was
ambiguous about *where* this authority lives. To be explicit, stated
once, up front:

**This specification does not modify, extend, or grant Customer access
to Aura Owner's internal employee portal or `StaffUser`/`StaffSession`
authentication in any way.** Aura Owner remains Action Aura's internal,
employee-only system (management, SUPER_ADMIN, sales, finance,
support). Everything below defines a **new, structurally separate**
external authority — its own model, its own session/token scheme, its
own routes, its own blueprint — that happens to be implemented as
server-side code within the same `owner/app/` codebase (because that is
where the canonical `Customer`/`Subscription`/`License`/`Installation`
data already lives), not as a feature of the employee portal. Item 7
below already states this explicitly ("must not extend or branch the
existing `app/auth/routes.py` staff login logic"); this note exists so
the *title* doesn't undercut that already-correct content.

**Not applied in this branch.** M7 proved customer-facing
authentication is absent from Owner (`customer-authentication-gap-
analysis.md`, six-point negative evidence). This is the exact
prerequisite specification M9 (activation orchestration) needs before
it can be classified anything other than blocked — a specification
only, no Owner code created or modified here.

## Real gap this closes

Today, Owner has exactly one auth blueprint (`app/auth/`,
`StaffUser`/`StaffSession`), 100% staff-facing. Every real device
activation is authenticated purely by possession (a license key +
device-key signature), never by a logged-in customer principal
(`customer-authentication-gap-analysis.md`). This specification defines
a new, parallel `CustomerAccount` authority — explicitly not a reuse of
`StaffUser`/`StaffSession`.

## 1. `CustomerAccount` authority

New model, `owner_customer_accounts` table, FK to the existing
`Customer` (`app/models/customers.py:14-49`) via `customer_id` — an
account belongs to a `Customer` organization, mirroring the real
existing `CustomerContact` relationship shape, not replacing it.
Real column set (adjusted to real evidence at implementation time):
`id`, `customer_id` (FK, NOT NULL), `email` (unique), `password_hash`,
`status`, `created_at`, `updated_at`.

## 2. Customer relationship

One `Customer` (organization) may have zero-or-more `CustomerAccount`
rows — mirrors the real `Customer` → `CustomerContact`/`CustomerAddress`/
`CustomerNote` 1:N pattern already established
(`owner-licensing-authority-audit.md`), not a new relational shape.
A `CustomerAccount` is scoped to exactly one `Customer`; it must never
implicitly grant access to a different `Customer`'s Licenses.

## 3. Verified email

`email_verified_at: DateTime | null`. Real requirement: an unverified
account must not be usable to authorize device activation, mirroring
why Owner already treats `StaffInvitation` acceptance as a gated flow
(`app/auth/routes.py:374`) rather than an immediate, unverified grant.

## 4. Password hashing

Must reuse the same real, already-audited Argon2id scheme
(`app/security/passwords.py`, min 12 chars, ≥3 of {lower, upper,
digit, symbol}) — not a weaker or bespoke scheme. Reusing the
*hashing utility module* is correct and intended; this is distinct
from reusing the `StaffUser` *table/session model*, which must not
happen (the checkpoint's own explicit prohibition).

## 5. Account status

Real states, mirroring `StaffUser.is_active`/`disabled_at`/
`disabled_reason` shape: `ACTIVE`, `DISABLED` (`disabled_at`,
`disabled_reason`), `PENDING_VERIFICATION`.

## 6. Enrollment / invitation

New flow, structurally parallel to but separate from
`StaffInvitation` (`app/auth/routes.py:374`,
`app/staff/services.py::create_staff_from_invitation`) — a
`CustomerAccountInvitation` issued by a staff member from the
Customer detail page (`app/customers/routes.py`), emailed, accepted
once, provisions exactly one `CustomerAccount`. Real open decision:
whether self-service signup (no staff invitation) is ever permitted —
not decided here; default assumption is staff-invitation-only, mirroring
the existing `StaffInvitation` pattern's own security posture.

## 7. Login

New route, new blueprint (e.g. `app/customer_auth/`, distinct from
`app/auth/`) — must not extend or branch the existing `app/auth/
routes.py` staff login logic; a genuinely separate code path, so a
future staff-auth security fix is never accidentally coupled to
customer-auth behavior or vice versa.

## 8. Logout

Symmetric to login — revokes the real session record (item 9), same
real pattern as `StaffSession` revocation (`session_version` bump or
row deletion), applied to the new, separate customer session table.

## 9. Access token or session contract

Real open decision for the implementer: server-side session (mirroring
`StaffSession`'s own opaque-token + hashed-storage pattern,
`app/security/tokens.py`) vs. a stateless signed token (JWT-like,
distinct from the licensing assertion signing key — must not reuse
`SigningKey`, which is scoped to license assertions, not customer
sessions). This spec recommends mirroring the real, already-audited
`StaffSession` pattern for consistency, but does not mandate it over a
stateless alternative if the implementer has a documented reason.

## 10. Refresh / revocation

Mirrors `StaffSession`'s own idle-timeout/absolute-lifetime/
`session_version`-bump-revokes-all pattern (`app/config.py:37-38`,
`app/auth/session.py:130-135`) — real, already-proven session-lifecycle
shape, reused as a *pattern*, not by sharing the same table.

## 11. Throttling

Real login-attempt throttling, mirroring whatever real mechanism
(if any) currently protects `app/auth/routes.py`'s own login route —
not independently audited in M7/M8; the implementer must locate and
mirror it, or add one if none currently exists for staff login either
(a real, open finding to confirm at implementation time, not assumed
either way here).

## 12. Password reset

Real, token-based, single-use, time-limited reset flow — mirroring the
real recovery-code single-use-consumption pattern already proven for
staff MFA (`app/security/mfa.py`, `RECOVERY_CODE_COUNT`,
`hash_recovery_code`) as a *pattern* for "single-use, hashed-at-rest,
consumed-once" discipline, not a literal reuse of MFA recovery codes
themselves.

## 13. MFA decision

Real open decision, not resolved by this spec: whether customer
accounts require MFA at all (staff accounts do, via TOTP,
`app/security/mfa.py`). Given the real activation protocol already
proves device possession cryptographically (Ed25519 device key), MFA
on the *account* layer may be lower-priority than it is for staff — a
Product Owner risk decision, not a technical one this spec can make
unilaterally.

## 14. Device activation authorization

Once `CustomerAccount` exists, a real design question this spec
raises but does not resolve: does device activation begin requiring a
logged-in `CustomerAccount` session (a real, additive security
tightening), or does it remain purely possession-based (license key +
device key) with `CustomerAccount` only used for account-management
UI (viewing/managing Installations, per `device-replacement-transfer-
contract.md`)? This spec recommends the latter as the minimal, most
backward-compatible option — existing possession-based activation
continues to work unchanged; `CustomerAccount` is additive, not a
replacement gate — but flags this as a real decision for the Product
Owner, not something M8/M9 may assume unilaterally.

## 15. Account-to-License ownership validation

`CustomerAccount.customer_id` must match a `License.customer_id`
before that account's session may view/manage that License's
Installations (`device-replacement-transfer-contract.md`'s
`ListInstallationsRequest`) — a real, straightforward FK-chain check,
analogous to how `app/customers/routes.py` already scopes CRM data to
one `Customer` at a time.

## 16. Privacy

`CustomerAccount.email` must be treated with the same real data-
minimization discipline as every other field audited in
`owner-data-minimization-contract.md` — never appears in a licensing
assertion payload, never logged in the licensing audit trail
(`ActivationEvent`), which remains device/installation-scoped, not
customer-account-scoped.

## 17. Audit

New `CustomerAccountAuditEvent` table (or reuse of Owner's existing
generic audit-log mechanism, if one exists — not independently
re-audited here) — login/logout/password-reset/invitation-accepted
events, mirroring the real, already-proven discipline that Owner's own
staff audit trail is immutable/append-only
(`accounting-governance.md`'s "expanded IMMUTABLE audit" pattern, cited
here only as a precedent for the *design principle*, not as Owner
code this spec depends on).

## 18. Rate limiting

Same real requirement as item 11 — mirror or establish real
per-account/per-IP throttling on login and password-reset endpoints.

## 19. Stable errors

Must reuse the same real anti-enumeration discipline already proven
for licensing (`licensing-error-contract.md`'s `to_public_reason_code`
normalization) — a customer-auth login failure must not distinguish
"account doesn't exist" from "wrong password" from "account disabled"
in its public response shape, for the same real security reason
Owner's licensing API already avoids it.

## M9 blocking classification

**M9 activation-orchestration work that depends on a logged-in
customer principal remains BLOCKED** until this specification (or an
explicitly Product-Owner-approved alternative secure enrollment
authority) is implemented server-side. Per item 14's own recommended
default, M9 work that only requires possession-based activation
(license key + device key, no customer login) is **not** blocked by
this gap — that path already has full real server support today
(M7's own audit). The blocking scope is narrower than "all of M9":
only customer-account-gated features (viewing/managing devices via a
logged-in session, self-service replacement requests) are blocked.
