# Phase 9.5C — Milestone 9: Interaction Domain Contract

## Service (`app.leads.engagement`)

`log_lead_interaction()` / `log_customer_interaction()` — one shared
internal `_log_interaction()` implementation (the two model classes,
`LeadInteraction`/`CustomerInteraction`, stay genuinely separate tables
per `crm-domain-reuse-matrix.md`; only the identical service logic is
deduplicated).

## Canonical interaction types (reused, unchanged)

`app.models.leads.INTERACTION_TYPES = ("CALL", "EMAIL", "MEETING",
"WHATSAPP_MANUAL_NOTE", "OTHER")` — already defined by Phase 9.5A.
`WHATSAPP_MANUAL_NOTE` is exactly what its name says: a manual log entry
recording that a WhatsApp conversation happened, never an actual
WhatsApp integration (forbidden this phase).

## Rules enforced

- Plain text only (`summary`, `Text` column, no HTML sanitization
  needed since no HTML is ever accepted or rendered unescaped — Jinja's
  autoescape handles display safety, matching every other free-text
  field in this codebase).
- Bounded length: 4000 characters (`LeadError("INTERACTION_SUMMARY_TOO_LONG")`).
- `occurred_at` cannot be more than 5 minutes in the future (small clock-
  skew tolerance, not a hard `<= now()`), rejected otherwise
  (`INTERACTION_OCCURRED_AT_TOO_FUTURE`).
- Actor cannot be spoofed: `employee_profile_id` is always the caller-
  supplied `actor_employee_profile_id` parameter, never taken from
  request data.
- Employee must have parent-record access — enforced at the route layer
  (Milestone 16) via the same ownership check already used for Lead/
  Customer detail routes, not inside this service (Non-Negotiable Rule
  10: service stays request-independent).
- No external email/WhatsApp/SMS action occurs — confirmed: this module
  has zero outbound network calls, zero references to any messaging
  provider SDK.

## Correction policy

Not implemented as a separate "edit" function this milestone — an
interaction is an append-only log entry (matches `LeadStatusHistory`'s
own append-only convention). A logged mistake is corrected by logging a
new interaction referencing the correction in its summary, not by
mutating history. If a dedicated edit/correction audit trail is needed
later, it is additive, not a retrofit of this function.

## Optional follow-up creation in the same transaction

Not implemented in this milestone as a single combined call — the
governing spec allows it ("interaction may optionally create a follow-up
through one transaction") but does not require it. `log_lead_interaction()`
and `create_lead_followup()` (Milestone 10) can already be called
sequentially inside one Flask request/transaction by a Milestone 16
route; a combined convenience function is a small, safe addition to make
later without needing today, not a blocking gap.
