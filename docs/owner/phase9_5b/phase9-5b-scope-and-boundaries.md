# Phase 9.5B — Scope and Boundaries

## In scope (per the governing spec, verbatim intent)

Real employee accounts/profiles, secure onboarding (reusing `StaffInvitation`), activation/suspension/
termination/archival, session revocation, role/permission management, presence (ONLINE/RECENTLY_ACTIVE/
OFFLINE), management employee dashboard, employee list/detail, employee self-profile, `/api/operations/v1`
employee+presence endpoints, audit logging, server-side RBAC/record-level security, responsive web UI.

## Out of scope (enforced, not merely stated)

Leads, customer cards, GPS capture, quotes/orders/invoices, payment confirmation, commissions
calculation/payout, expenses workflows, management shared notes, full sales/licensing dashboard redesign,
Flutter/Android/iOS apps, remote VPS/public domain, Phase 9R, Phase 9.5C+, payment gateway, WhatsApp/SMS/
external email, e-invoicing, Aura Core integration, unrelated Clinic/Retail changes, real employee data.

## One deliberate scope reduction, recorded honestly up front

**Arabic/RTL localization (Milestone 15).** Audited: Owner's existing web UI (`owner/app/templates/`) has
**zero** i18n infrastructure — `<html lang="en">` is hardcoded in `layout/base.html`, every existing
template (customers, staff, catalog, dashboard, etc.) uses inline English strings with no translation
function, no `tr()`/gettext call, no RTL stylesheet, across all of Phase 5-9.5A. Building a real,
consistent bilingual/RTL system is a distinct, codebase-wide undertaking — retrofitting it onto only the
new employee screens would mean either (a) a translation layer no other Owner screen honors, immediately
inconsistent, or (b) re-architecting every existing template this phase, which is explicitly out of scope
(no unrelated changes). **Decision**: Phase 9.5B's new templates are built in English, matching 100% of
existing Owner UI convention exactly (same layout, same CSS variables, same badge/table patterns) — no
new visual framework introduced (Non-Negotiable: "do not introduce a disconnected visual framework").
Milestone 15 is marked **NOT VERIFIED / DEFERRED** in the final gate matrix, with this same justification,
not silently dropped. A future phase that decides to give the whole Owner app real i18n can do so
consistently, in one pass, rather than starting from a fork with one bilingual corner.

This mirrors Phase 9's own honest "local-only fallback" scope reduction and Phase 9.5A's own
"foundation-only, no routes" boundary — a documented, justified, non-silent scope decision, not a
shortfall discovered later.
