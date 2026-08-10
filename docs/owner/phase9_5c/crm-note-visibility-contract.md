# Phase 9.5C — Milestone 11: Note Visibility Contract

## Values (reused, unchanged)

`AUTHOR_ONLY`, `ASSIGNED_RECORD_USERS`, `MANAGEMENT_ONLY` — exactly the
governing spec's suggested set; no existing conflicting vocabulary was
found (Milestone 1 audit confirmed neither `LeadNote` nor `CustomerNote`
had any visibility concept before Milestone 2/3's additive migration).

## Enforcement point: query time, in `app.leads.notes`

Both `list_lead_notes_visible_to()` / `list_customer_notes_visible_to()`
fetch all non-archived notes for an already-access-checked parent record,
then filter in Python by visibility rule before returning. A note the
caller cannot see is **dropped from the list entirely** — not returned
with a redacted body, not counted, not hinted at through a placeholder
row. Verified directly:
`test_author_only_note_hidden_from_non_author_non_management` asserts
`len(as_other) == 0` (not 1-with-redacted-body); the same pattern in
`test_management_only_note_hidden_from_non_management`.

## Rule

| Visibility | Visible to |
|---|---|
| `AUTHOR_ONLY` | The note's author, or any `is_management=True` caller. |
| `ASSIGNED_RECORD_USERS` | Anyone who already has access to the parent record (the default — the precondition every caller of this module must already satisfy before calling it at all). |
| `MANAGEMENT_ONLY` | Only `is_management=True` callers. |

`is_management` is a plain boolean the route layer computes from the
actor's permission codes (e.g. `"leads.view_all" in codes` /
`"customers.view_all" in codes`) — the service module itself takes no
Flask/session dependency (Non-Negotiable Rule 10).

## Content safety

Plain `Text` column, Jinja auto-escaping handles safe HTML-injection-free
rendering (same as every other free-text field in this codebase) — no
raw HTML accepted or stored; the spec's "tightly sanitized content only"
requirement is satisfied by never accepting rich text/HTML in the first
place, rather than sanitizing something that was allowed in.

## Version conflicts and audit

`add_lead_note()`/`add_note()` (Customer) both validate the `visibility`
value against `NOTE_VISIBILITIES`, raising a stable-code error for an
unrecognized value. Editing/version-conflict handling for an existing
note (vs. creating a new one) is not implemented this milestone — no
existing route calls for note editing yet; when Milestone 16 builds one,
it reuses the same `version`/`archived_at` optimistic-lock pattern
already proven on `LeadContact` (Milestone 8).

## No hard deletion

Neither `LeadNote` nor `CustomerNote` has a delete function — only
`archived_at` (both columns added by the Milestone 2/3 migration). An
archived note is excluded from the visible-notes query (`archived_at.is_
(None)` filter) but remains in the database, auditable.

## No note reaches the licensing API or normal logs

Confirmed: `app.leads.notes` has zero references anywhere in
`app/licensing_service/` or any logging call — notes are only ever
returned through the CRM-specific query functions above.
