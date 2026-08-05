# Owner Internal vs. Customer Boundary

Authoritative reference for a distinction that must never blur across
any future Owner-server or mobile-client work: **Aura Owner is Action
Aura's internal company-management system.** It is not, and must never
become, a Retail/Clinic customer-facing application.

## The four layers

```
┌───────────────────────────────────────────────────────────────────┐
│ 1. Aura Owner Employee Portal                                       │
│    Internal Web UI. Used ONLY by Action Aura personnel:             │
│    management, SUPER_ADMIN, sales, finance, support, operations.    │
│    Auth: StaffUser / StaffSession (app/auth/, app/models/staff.py). │
│    Argon2id passwords, TOTP MFA, SQL-backed RBAC, server-side       │
│    opaque-token sessions. Real, implemented, fully tested           │
│    (customer-authentication-gap-analysis.md's "real staff auth"     │
│    section). Manages: employees, Leads, Customers, Contacts,        │
│    follow-ups, Quotes, approvals, Orders, Invoices, Payments,       │
│    allocations, Refunds, Commissions, Expenses, cash closing,       │
│    reports, Subscriptions, Licenses, Installations, Product         │
│    releases, private distributions, audit.                          │
└───────────────────────────────────────────────────────────────────┘
                              │
                              │ internal service calls only
                              ▼
┌───────────────────────────────────────────────────────────────────┐
│ 2. Aura Owner Internal Application Services / Licensing Domain      │
│    The canonical, single source of truth for Customer commercial    │
│    ownership, Subscription status, License lifecycle,               │
│    License.device_limit, Product entitlement, Platform              │
│    eligibility, Installation status, activation eligibility,        │
│    final device-slot concurrency, suspension, expiry, renewal,      │
│    revocation, release channel, release authorization, audit.       │
│    Retail/Clinic clients CONSUME these decisions. They never        │
│    independently decide them (owner-licensing-authority-audit.md,   │
│    subscription-entitlement-license-map.md).                        │
└───────────────────────────────────────────────────────────────────┘
                              │
                              │ exposed through a bounded, separate surface
                              ▼
┌───────────────────────────────────────────────────────────────────┐
│ 3. External Customer / Licensing API                                │
│    TODAY (real, implemented): device-level authentication only --   │
│    Ed25519 device-key signature + license-key HMAC                  │
│    (app/api_external/routes.py, device_identity.py). A DEVICE       │
│    proves possession of a license key and a device key. There is    │
│    no customer login in this flow at all.                           │
│                                                                       │
│    FUTURE (specified, not built --                                  │
│    OWNER-EXTERNAL-CUSTOMER-IDENTITY-AND-LICENSING-BOUNDARY-SPEC.md):│
│    an isolated CustomerAccount authority -- own model, own           │
│    session/token scheme, own routes, own blueprint (e.g.             │
│    app/customer_auth/, explicitly distinct from app/auth/) --        │
│    bounded to customer/device operations only: enrollment,           │
│    verified email, login/logout, License ownership validation,       │
│    activation authorization, Installation management, device         │
│    replacement. STRUCTURALLY SEPARATE from StaffUser/StaffSession    │
│    at every layer -- different table, different session store,       │
│    different routes, different permissions, different rate limits,   │
│    different audit classification. May be implemented as code        │
│    inside owner/app/ (because that's where the canonical data        │
│    lives) without being part of, or granting access to, the          │
│    employee portal in layer 1.                                       │
└───────────────────────────────────────────────────────────────────┘
                              │
                              │ HTTPS, bounded licensing/customer endpoints only
                              ▼
┌───────────────────────────────────────────────────────────────────┐
│ 4. Retail / Clinic Clients (incl. Unified Mobile)                    │
│    Communicate ONLY with layer 3's explicitly exposed                │
│    customer/licensing endpoints. Never see, call, or authenticate    │
│    against layer 1's employee routes/sessions/cookies/MFA/RBAC.      │
│    A valid commercial License authorizes the Installation to use     │
│    the product -- it does not, and must never, grant the Customer    │
│    or any device access to Aura Owner's employee portal or           │
│    internal data.                                                    │
└───────────────────────────────────────────────────────────────────┘
```

## Three separate identity concepts — never interchangeable

1. **Aura Owner employee identity** (`StaffUser`) — Action Aura's own
   personnel operating the business through the internal portal.
2. **External Customer / licensing identity** — today, purely
   device-possession-based (Ed25519 key + license key); in the future,
   optionally an isolated `CustomerAccount` for account-management
   self-service. Never a `StaffUser` row, never an Owner role.
3. **Local Retail/Clinic business-user identity** — e.g. a cashier
   signing into the local Retail app with its own existing employee/PIN
   model. Entirely unrelated to both of the above; a local Retail
   administrator cannot override License expiry/revocation/device
   limits, and cannot access Owner internal data.

## Why this document exists

M7's own real, evidence-based finding —
`customer-authentication-gap-analysis.md`'s "customer-facing login/
portal identity: MISSING" — is correct and unchanged. What this
document corrects is *interpretation*: that finding means a **new,
isolated external boundary** is not yet built (layer 3's future half),
never that Aura Owner's internal employee portal (layer 1) should be
opened to customers, and never that Owner employee authentication
itself is deficient — it is real, implemented, and tested exactly as
`customer-authentication-gap-analysis.md`'s own "real staff auth"
section describes.

## Prohibited designs (restated as a checklist)

- [ ] Customer accounts created as Owner Employees — **prohibited**
- [ ] SALES/SUPPORT/FINANCE/VIEWER/SUPER_ADMIN roles assigned to Customers — **prohibited**
- [ ] Owner employee login endpoints reused in Retail/Clinic — **prohibited**
- [ ] Owner employee cookies/sessions reused in mobile clients — **prohibited**
- [ ] Owner CRM/Finance/Expenses/reports/employees/audit UI exposed to Customers — **prohibited**
- [ ] A mobile app authenticating via an Owner employee session — **prohibited**
- [ ] Customer passwords stored in the local Retail business database — **prohibited**
- [ ] `CustomerAccount` and `StaffUser` tables merged without explicit isolation — **prohibited**
- [ ] A Customer Contact automatically becoming an Owner employee — **prohibited**
- [ ] Local Retail users treated as Aura Owner employee identities — **prohibited**
- [ ] A valid License treated as permission to access Aura Owner — **prohibited**
- [ ] An Owner employee role treated as ownership of a Customer License — **prohibited**

Every item above remains true across M7 (audit/contract only, no code)
and M8 (device-policy/iOS contract work, no Owner code, no customer
auth implementation) to date — confirmed by direct evidence, not
assumed: `git log --name-only f2b7b3f..feat/retail-unified-mobile-android-ios`
(the Unified Mobile effort's own real entry-gate commit through current
HEAD) shows **zero** files under `owner/` touched by any commit in that
range. (A naive `branch ^master` diff is *not* the right comparison
here — `master` predates most of Owner's own Phase 5-9.5E development,
so it would wrongly attribute that unrelated ancestral history to this
branch; the commit-range diff above is the correct, precise check.)
`customer-authentication-gap-analysis.md`/
`OWNER-EXTERNAL-CUSTOMER-IDENTITY-AND-LICENSING-BOUNDARY-SPEC.md` are
both explicit that nothing here is applied server-side, consistent with
this evidence.

## Acceptance confirmation (Product Owner boundary review)

Recorded explicitly, each point checked against real evidence already
cited in this branch — not asserted, verified:

1. **Aura Owner is an internal Action Aura employee system** — layer 1
   above; `app/auth/`, `app/models/staff.py`.
2. **Owner employee authentication remains internal** — unchanged by
   M7/M8; zero commits touch `owner/` (git range above).
3. **Customers do not log into Aura Owner** — no customer login route
   exists anywhere in the codebase today
   (`customer-authentication-gap-analysis.md`); layer 3's future
   `CustomerAccount` is explicitly a separate blueprint
   (`app/customer_auth/`), never `app/auth/`.
4. **External Customer identity is a separate future authority** —
   `OWNER-EXTERNAL-CUSTOMER-IDENTITY-AND-LICENSING-BOUNDARY-SPEC.md`,
   specified only, not applied.
5. **Retail and Clinic use bounded licensing endpoints only** — layer
   3/4 boundary above; `app/api_external/routes.py`, device-key +
   license-key possession auth, no employee session ever reachable
   from a client.
6. **Local Retail users are separate from both Owner employees and
   external Customer identities** — "Three separate identity concepts"
   section above.
7. **No code was changed to merge these authorities** — zero `owner/`
   files touched this branch (git range above); M8 work is Kotlin
   contract models + docs only (`milestone-8-test-report.md`'s
   cross-language-checks section).
8. **No Customer was added to Owner employee RBAC** — no RBAC file
   touched; `CustomerAccount` (spec item 1) is a distinct table from
   `StaffUser`, never a role grant.
9. **No employee login endpoint was selected for mobile activation** —
   `milestone-8-decision.md` gate table: "No real HTTP activation
   implemented — PASS," "No Owner code modified — PASS"; mobile's real
   activation path remains possession-based only (license key + device
   key), never `StaffSession`.

**All future server work preserves this separation** — binding on any
Owner-side implementation of `OWNER-EXTERNAL-CUSTOMER-IDENTITY-AND-
LICENSING-BOUNDARY-SPEC.md` or `OWNER-M8-IOS-MULTIDEVICE-CHANGE-
SPEC.md`: both require their own separately approved branch/worktree
(`milestone-8-decision.md`'s "Proceed to Milestone 9" section), neither
is authorized by this confirmation to be implemented as part of M8/M9.

Confirmed 2026-08-05. M8 stands closed at PASS
(`milestone-8-decision.md`). **M9 does not begin under this
confirmation** — remains blocked per that decision's own stated
conditions (separately-approved Owner branch/worktree plan; explicit
customer-auth implementation decision), both still outstanding.
