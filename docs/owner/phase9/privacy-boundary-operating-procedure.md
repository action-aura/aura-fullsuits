# Phase 9 — Privacy Boundary Operating Procedure

## The boundary, restated as an operating rule (not just an architecture fact)

Anyone operating Owner — support staff answering a pilot customer's question, an infra operator
debugging an issue, a developer reading logs — must never ask a pilot customer to send patient records,
appointment details, prescriptions, diagnoses, Clinic notes, sales data, receipts, stock data, supplier
data, or Retail customer records through any Owner-adjacent channel (support ticket, email to the
Owner team, etc.) to "help debug" something. If diagnosis genuinely requires seeing that data, it stays
on the customer's own device/product installation (Retail/Clinic), inspected by the customer or via a
screen-share of *their* product's own UI — never uploaded to or stored by Aura Owner or its operators.

## Why this is an operating procedure, not just an architecture diagram

The architecture (`network-and-trust-boundaries.md`) already makes it structurally hard for this data
to reach Owner through the product's own normal operation. The real risk this procedure addresses is a
*human* one: a well-meaning support interaction where someone pastes a screenshot or exports a file
"just to help" — that's the actual, realistic way this boundary could be violated, not a code path.

## If it happens anyway

Treated as the "suspected customer-domain data leakage" row in `incident-response-plan.md` (P0) —
stop the specific data flow, do not simply delete the data and move on (deletion without understanding
how it got there doesn't prevent recurrence), review how it happened, update this procedure and/or
staff training if the cause was procedural rather than technical.

## Correction requests

If a customer identifies incorrect data Owner does hold about them (customer legal name, contact
details, license/subscription terms), correction goes through the same real, existing Owner services
used to create that data in the first place (never a raw DB edit) — audited the same way any other
change is.
