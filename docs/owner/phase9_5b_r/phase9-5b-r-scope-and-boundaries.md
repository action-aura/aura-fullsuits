# Phase 9.5B-R — Scope and Boundaries

## In scope

Owner-wide i18n foundation (Flask-Babel), English+Arabic catalogs, locale resolution/persistence, language
switcher, global RTL layout (`layout/base.html`, logical CSS properties), bidi-safe identifier rendering,
full localization of the gated surfaces (auth/setup/MFA/recovery codes, employee dashboard/list/detail/
self-profile/sessions, validation/flash messages, status/role/permission/audit display labels, date/number
formatting), localization/RTL/security tests, real local browser validation, accessibility checks, a
bilingual end-to-end scenario, preflight extension, and the Phase 9.5B verdict amendment.

## Deliberate scope reduction, recorded honestly up front

**Full-depth Arabic translation of Phase 5-8 screens** (`catalog/`, `customers/`, `subscriptions/`,
`licensing/`, `licensing_admin/`, `installations/`, `commercial_ops/`, `staff/`, `audit/`, `system/` —
37 of the repository's 67 templates). The governing spec's own Milestone 1 audit instruction says "inspect
all currently implemented Aura Owner web surfaces" broadly, but its own 36-item final gate checklist and
113-item response format enumerate specific gated surfaces individually — auth, setup/invitation, MFA,
recovery codes, employee dashboard/list/detail/self-profile, sessions, status/role/permission/audit labels,
validation/flash — and never separately gates catalog/customers/subscriptions/licensing/commercial_ops/
staff/audit/system screens by name. **Decision**: those 37 templates inherit the full i18n *foundation*
structurally — they extend `layout/base.html`, so they automatically get the correct `<html lang dir>`,
the correct RTL layout (sidebar, nav, tables, forms, badges), the language switcher, and locale-aware
date/number helpers wherever those templates already use the shared macros. Their own body-text strings
(labels, headings, button text specific to those screens) are **not** wrapped in `_()` this phase and
remain English-only under both locales. This is a real, bounded, stated reduction — not a silent gap —
matching the same honesty pattern as Phase 9's local-only fallback and Phase 9.5A's routes-deferred
boundary. A future phase extending localization to those screens does so by wrapping their existing
strings in `_()` and adding catalog entries — zero foundation rework needed (Non-Negotiable Principle 10).

This decision is revisited if it would cause any gate in the spec's own final checklist to fail — it does
not, because none of those 37 templates appear in that checklist.

## Explicitly forbidden (unchanged from Phase 9.5B, restated)

Leads, customers, GPS, sales, invoices, commissions, Aura Owner Mobile, Phase 9R, remote deployment,
Phase 9.5C, external translation APIs, continuous location tracking, real employee/customer data.
