# Phase 9.5C — Milestone 13: Conversion History Preservation Strategy

## Decision: link, don't duplicate

Pre-conversion interactions, follow-ups, notes, and locations remain
attached to the retained `Lead` row (`lead_id`-scoped tables) rather
than being copied to their `Customer`-side counterparts
(`CustomerInteraction`/`CustomerFollowup`/`CustomerNote`/
`CustomerLocation`). The `Lead` row itself is never deleted — it is
reachable from the converted `Customer` via `Customer.
converted_from_lead_id`.

## Why (not "no duplicate history" restated — the actual reasoning)

1. **Correctness**: copying would create two independent, divergent
   copies of the same historical fact under two different parent
   records — the exact failure mode the governing spec's own
   Non-Negotiable Rule 12/13 auditability requirements guard against.
   The original interaction/note/location was authored *about the Lead,
   at a point when it was a Lead* — rewriting its parent to "Customer"
   after the fact would misrepresent when/what it was actually recorded
   against.
2. **`CustomerLocation`'s own CHECK constraint makes copying impossible
   without violating a real database invariant**: a location row can
   only ever have `lead_id` XOR `customer_id` set, never both, and there
   is no "this row was copied from lead X" provenance column. Forcing a
   copy would require inventing new schema just to represent something
   that's already fully representable via the existing
   `converted_from_lead_id` lineage pointer.
3. **Contacts are the one real exception**, and are copied (Milestone 8/
   13) — because a contact represents an ongoing, present-tense
   relationship ("this is how to reach this organization") that the
   Customer genuinely needs its own live copy of to keep managing going
   forward, unlike a log of what already happened.

## What a future detail page (Milestone 7) must do

A Customer's detail page's "history" sections should query **both**:
- The Customer's own `CustomerInteraction`/`CustomerFollowup`/
  `CustomerNote`/`CustomerLocation` rows (post-conversion activity), and
- The linked Lead's `LeadInteraction`/`LeadFollowup`/`LeadNote`/
  `CustomerLocation` (`lead_id`-scoped) rows, when `Customer.
  converted_from_lead_id IS NOT NULL` (pre-conversion history).

This is a read-time UNION, not a write-time copy — documented here so
Milestone 7's implementation follows this decision rather than
re-deriving it (or worse, "fixing" the apparent gap by adding a copy
step that would then create the exact duplication this document explains
was deliberately avoided).
