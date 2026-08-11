# Phase 9.5C — Milestone 2: Lead Transition Matrix (implementation-level)

Exact table encoded in `app/leads/errors.py` / `app/leads/services.py`.

```python
LEAD_TRANSITIONS: dict[str, set[str]] = {
    "NEW":                {"POTENTIAL", "NOT_INTERESTED_NOW", "LOST"},
    "POTENTIAL":          {"FOLLOW_UP", "NOT_INTERESTED_NOW", "LOST"},
    "FOLLOW_UP":          {"UNDER_OBSERVATION", "NOT_INTERESTED_NOW", "LOST"},
    "UNDER_OBSERVATION":  {"QUALIFIED", "NOT_INTERESTED_NOW", "LOST"},
    "QUALIFIED":          {"NOT_INTERESTED_NOW", "LOST"},  # CONFIRMED only via convert()
    "NOT_INTERESTED_NOW": {"ARCHIVED"},
    "LOST":               {"ARCHIVED"},
    "CONFIRMED":          {"ARCHIVED"},
    "ARCHIVED":           set(),
}
```

`CONFIRMED` never appears as a key's *target* in `change_lead_status()`
— it is only ever set by `conversion.py:convert()`, which performs its
own eligibility check (`status in ("LOST", "ARCHIVED")` rejected, now
additionally `status == "CONFIRMED"` rejected — re-conversion guard)
before flipping status, and appends its own `LeadStatusHistory` row
(the Milestone 13 fix).

## Reason requirement

Only the `-> LOST` transitions require a non-empty `reason`. Every other
transition accepts an optional reason (stored if provided, not required).

## Self-transition

Not present in any target set — `change_lead_status(lead, lead.status,
...)` always raises `INVALID_LEAD_TRANSITION`, avoided at the UI layer by
simply not offering the current status as a target option.
