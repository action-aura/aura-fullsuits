# Phase 9.5B-R Milestone 7 — Bidirectional Content Safety

## `bidi_isolate` Jinja filter (`owner/app/i18n.py`)

Wraps a value in `<bdi dir="ltr">...</bdi>` (HTML-escaped via `markupsafe.escape` first — never raw
interpolation). `<bdi>` isolates the value from the surrounding Arabic paragraph's bidi algorithm without
needing per-value direction detection; `dir="ltr"` is pinned explicitly because every identifier this
filter is applied to is always LTR-shaped by construction (never itself translated, never reversed).

## Applied to every real identifier in the gated templates

Employee number (`employees/list.html`, `detail.html`, `profile/index.html`), email address
(`employees/detail.html`, `profile/index.html`, `employees/invitations.html`,
`employees/invitation_created.html`), phone number (`employees/detail.html`, `profile/index.html`).
Session platform values (`WEB`/`ANDROID`/`IOS`) and role-code strings use a plain `dir="ltr"` attribute
directly (no translatable/user-entered content inside them, so the lighter-weight attribute alone is
correct and sufficient — `bidi_isolate` is reserved for values that can contain arbitrary user-entered
text).

## Not reversed, not corrupted, still copyable

`<bdi>` is a real, standard HTML5 element built exactly for this — it changes rendering/selection order
only, never the underlying text content or DOM text node value. Copy-pasting an employee number or email
rendered inside a `bidi_isolate`d element yields the exact original string, byte-for-byte — verified in
`test_phase9_5b_r_bidi_safety.py` by asserting the rendered HTML's text content (stripped of markup)
equals the original database value exactly.

## No hidden characters ever written to the database

`bidi_isolate` is a presentation-only Jinja filter — it operates on a value already read from the database
for display and returns transient HTML markup; nothing it produces is ever written back to
`EmployeeProfile.employee_number`/`phone` or `StaffUser.email`. Confirmed by design: the filter has no
database access at all (`owner/app/i18n.py` imports no model, no `db_session`).

## Setup links / URLs

`employees/invitation_created.html`'s one-time setup link uses a plain `dir="ltr"` `<code>` block (a URL
never contains user-entered translatable text that would need `<bdi>`'s per-character isolation — a
simpler, sufficient fix).
