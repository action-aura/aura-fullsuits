# Owner App — First-Login Employee Welcome Contract (Stage C)

Real implementation reference for the product tour added this stage.
Not a customer onboarding flow — this is for Action Aura employees
created by an administrator (via `StaffInvitation` /
`auth/accept-invitation`, per `auth/routes.py`), landing on their own
role's dashboard for the first time.

## Files

- `owner/app/templates/layout/_tour.html` — server-rendered tour data
  and step markup, `{% include %}`d only from `dashboard/index.html`
  (the real post-login/post-MFA landing page).
- `owner/app/static/js/product-tour.js` — generic step-navigation
  engine (open/close, back/next/finish, dot indicator, focus trap,
  Escape to close). Contains no business data or permission logic —
  only reads what `_tour.html` already rendered.
- `owner/app/static/css/components.css` (`.aura-tour-*` classes) —
  layout only, tokens throughout.
- `owner/app/templates/layout/base.html` — profile menu link
  ("Show welcome tour" → `dashboard.index?tour=reopen`) and the
  `product-tour.js` script include.

## Why no database migration

Per the governing checkpoint's explicit instruction ("do not add a
migration merely to remember a theme or product tour"): `StaffUser`
has no onboarding-completion field, and none was added. Completion is
tracked entirely client-side —
`localStorage["aura-owner-tour-seen-<staff.id>"]` — scoped per staff
ID (not just per browser) so it behaves correctly even if multiple
employees share a machine. This is a real, disclosed limitation: if an
employee clears site data or uses a different browser/device, the tour
reappears once. That is an acceptable, explicitly accepted trade-off
for a non-security, non-business-record preference — never treated as
a durable fact about the employee's onboarding status anywhere else in
the system.

**Reopening**: the profile menu's "Show welcome tour" link navigates to
`dashboard.index?tour=reopen`, which force-opens the tour regardless of
the stored "seen" state (`_tour.html`'s `data-tour-force` attribute,
read by `product-tour.js`), then strips the query param via
`history.replaceState` so a page refresh doesn't reopen it again.

## Steps (real data only, per the governing spec's 9-step outline)

1. **Welcome to Aura Owner** — static copy, no per-employee data.
2. **Your account** — `staff.display_name` and real role label
   (`staff.is_super_admin` → "Super Admin", else
   `role_label(staff.role_assignments[0].role.code)` — the same i18n
   helper already used elsewhere in the app, not a new lookup).
3. **Your work areas** — a real, permission-checked list (`has_permission`/
   `has_any_permission`, the same calls `layout/_sidebar.html` uses for
   its own group visibility — intentionally mirrored rather than shared
   via a macro, since this is presentation-only and duplication risk
   here is a stale tour list, not a security question). No area is
   listed for an employee who cannot actually reach it.
4. **Account security** — real `staff.mfa_credential`
   presence/confirmation state. Never claims MFA is enabled when it
   isn't, and never exposes any credential detail.
5. **Language and appearance** — points at the real, already-working
   top-bar language and theme controls (`light-dark-theme-contract.md`)
   — no separate preference-setting mechanism invented here.
6. **Getting around** — sidebar/profile-menu orientation, static copy.
7. **Quick actions and completion** — real, permission-gated links
   (View Leads / View Quotes / View Licenses / View Employees, each
   only rendered if the employee actually holds the relevant
   permission) plus the finish action, which marks the tour seen and
   returns to the dashboard already being viewed.

No hidden permission is ever implied by a tour step being shown, and
no step claims a role grants permissions the employee doesn't actually
have — every conditional in `_tour.html` is a real `has_permission()`
call against the same RBAC source of truth as the rest of the app.

## Accessibility / motion

Real `role="dialog"` `aria-modal="true"` `aria-labelledby`, focus
moves into the dialog on open and returns to the triggering element on
close, `Escape` closes, click on the overlay backdrop closes. Step
transitions are an instant `hidden` toggle (no animation to begin
with), so there is nothing additional to suppress under
`prefers-reduced-motion` — noted explicitly in `product-tour.js` so a
future animated version doesn't forget the check.

## What this is not

Not a security control, not a durable onboarding record, not a
customer-facing flow, and not a replacement for the real MFA
enforcement already in `auth/routes.py` (an employee with
`mfa_required=True` is forced through real MFA enrollment before ever
reaching the dashboard/tour at all — the tour's security step is purely
informational for employees where MFA is optional).
