# Phase 9 — Incident Communication Templates

No secret values, no internal system details beyond what the customer needs, ever included.

## Initial notification (P0/P1 affecting the pilot customer)

> Subject: [Aura] Service issue — we're on it
>
> We identified an issue affecting [Retail/Clinic] staging service at [time]. We're actively
> investigating. Your data is not at risk — this is a service availability issue, not a data issue
> [remove/adjust if untrue for the specific incident]. We'll update you within [response target from
> `support-escalation-matrix.md`].

## Resolution notification

> Subject: [Aura] Service issue resolved
>
> The issue reported at [time] is resolved as of [time]. Root cause: [plain-language summary, no
> internal jargon]. What we changed to prevent recurrence: [summary]. No action needed from you unless
> noted below. [Any customer action needed, if applicable.]

## Data-related incident (only used if genuinely a data issue — never used for a plain availability issue)

> Subject: [Aura] Important: data incident notice
>
> [Factual, specific description of what happened, what data was involved, what was NOT involved (e.g.
> "no patient/sales data was involved — Aura Owner does not store this"), what we've done, what you
> should do if anything.]

## Planned maintenance notice (used before, e.g., a pepper rotation per `key-rotation-runbook.md`)

> Subject: [Aura] Planned maintenance on [date]
>
> We'll be performing planned maintenance on [date, time, expected duration]. [Service] will be
> briefly unavailable / you may need to re-enter your license key afterward [only if true for the
> specific maintenance].
