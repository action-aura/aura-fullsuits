# Phase 9.5B-R2 — Complete Domain Label Catalog

Real functions added to `app/i18n_labels.py` this wave (all following the
established pattern: dict built per-call, never module-level; safe
`.get(code, code)` fallback):

| Function | Real codes covered |
|---|---|
| `subscription_status_label` | DRAFT, PILOT, ACTIVE, PAST_DUE, SUSPENDED, EXPIRED, COMPLETED, CANCELLED |
| `license_status_label` | DRAFT, ISSUED, ACTIVE, SUSPENDED, REVOKED, EXPIRED, REPLACED |
| `installation_status_label` | REGISTERED, PENDING_ACTIVATION, ACTIVE, SUSPENDED, DEACTIVATED, REPLACED |
| `customer_status_label` | LEAD, PROSPECT, PILOT, ACTIVE, SUSPENDED, CLOSED, ARCHIVED |
| `renewal_status_label` | DRAFT, QUOTED, AWAITING_CONFIRMATION, AWAITING_PAYMENT, PAYMENT_RECORDED, APPROVED, APPLIED, REJECTED, CANCELLED, VOIDED |
| `pilot_status_label` | DRAFT, APPROVED, ACTIVE, EXTENDED, CONVERTED, COMPLETED, CANCELLED |
| `pilot_conversion_decision_label` | PENDING, CONVERT, DO_NOT_CONVERT |
| `notification_severity_label` | INFO, WARNING, CRITICAL |
| `notification_status_label` | OPEN, IN_PROGRESS, ACKNOWLEDGED, RESOLVED, DISMISSED |
| `pending_activation_status_label` | PENDING_REVIEW, APPROVED, REJECTED |
| `backup_status_label` | SUCCESS, FAILED, IN_PROGRESS |
| `signing_key_status_label` | DRAFT, ACTIVE, RETIRED, REVOKED |
| `device_key_status_label` | ACTIVE, REVOKED |
| `emergency_extension_status_label` | ACTIVE, REVOKED |
| `activation_mode_label` | AUTOMATIC, MANUAL_APPROVAL, RISK_REVIEW |
| `activation_event_type_label` | ACTIVATION, CHECK_IN, DEACTIVATION |
| `activation_event_result_label` | ACCEPTED, REJECTED, PENDING, SUCCESS, FAILURE |
| `audit_result_label` | SUCCESS, FAILURE |
| `health_status_label` | OK, DEGRADED, DOWN, UNKNOWN |
| `billing_model_label` | ONE_TIME, MONTHLY, ANNUAL, PILOT, CUSTOM |
| `product_commercial_status_label` | DRAFT, PLANNED, PILOT, AVAILABLE, RETIRED |
| `plan_lifecycle_status_label` | DRAFT, PLANNED, PILOT, AVAILABLE, RETIRED |
| `addon_availability_status_label` | DRAFT, PLANNED, PILOT, AVAILABLE, RETIRED |
| `payment_status_label` | PENDING, CONFIRMED, FAILED, REFUNDED, VOIDED |
| `renewal_date_rule_label` | EARLY_RENEWAL_FROM_CURRENT_END, LATE_RENEWAL_FROM_APPROVAL_DATE |
| `timeline_category_label` | SUBSCRIPTION, LICENSE, INSTALLATION, RENEWAL, PILOT, NOTIFICATION |
| `generic_audit_action_label` | 25 real system-wide audit action codes (extended once this wave after real dev-DB data revealed 5 missing codes) |

Plus reuse of Phase 9.5B-R's existing `role_label`, `account_status_label`,
`mfa_status_label` functions in new contexts (staff admin screens,
notification role filters) — no duplicate mapping created.

## One deliberate non-mapping (documented, not an omission)

`SecurityEvent.event_type` (audit/security_events.html) has no known
populated values anywhere in this codebase (the table is never written to
by any code path found — see `docs/owner/phase9_5b_r/rtl-visual-defect-log.md`'s
equivalent Phase 9.5B-R finding) — displayed as a raw, `<bdi dir="ltr">`-wrapped
identifier rather than inventing an unverified enum mapping.
