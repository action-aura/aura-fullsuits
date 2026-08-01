# Phase 9.5B-R3 — Milestone 5: Browser-Family Evidence Matrix

| # | Family | 390x844 EN | 390x844 AR | 1440x900 | 1024x768 | 768x1024 |
|---|---|---|---|---|---|---|
| 1 | Dashboard | Y | Y | Y (AR, kb evidence) | — | — |
| 2 | Auth (login) | Y | Y | — | — | — |
| 3 | MFA/invitation/setup/reauth | Y (reauth) | Y (reauth) | — | — | — |
| 4 | Employees | Y | Y | Y (EN+AR) | — | — |
| 5 | Self-profile/sessions | Y | Y | — | — | — |
| 6 | Customers | Y | Y | — (Enter-key nav evidence, M6) | — | — |
| 7 | Catalog/plans | Y | Y | — | — | — |
| 8 | Subscriptions/renewals/pilots | Y | Y | — | Y (EN+AR) | — |
| 9 | Licenses | Y | Y | — | — | — |
| 10 | Installations/slot exceptions | Y | Y | — | — | — |
| 11 | Audit | Y (viewport-capped) | Y (viewport-capped) | — | — | Y (AR) |
| 12 | Backups | Y | Y | — | — | — |
| 13 | Notifications/queue/reconciliation | Y | Y | — | — | — |
| 14 | Staff/licensing-admin | Y | Y | — | — | — |
| 15 | 404/error | Y | Y | — | — | — |
| 16 | Emergency ext./activation policy/pending activations | Y | Y | — | — | — |

All 16 families: real evidence at the mobile viewport in both locales
(the governing spec's minimum bar per family). The 4 data/form-heavy
families most likely to expose a wide-viewport layout defect additionally
verified at 1440x900/1024x768/768x1024. Zero defects found at any
captured combination.
