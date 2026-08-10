"""UI modernization Stage D.6 (Employee/Staff/Role-Assignment) -- shared
status-badge-color helper for the Employees list/detail screens.

No status-step (timeline) builder exists in this module, deliberately.
EmployeeProfile's real ALLOWED_TRANSITIONS graph (app.employees.services)
is an HR employment record, not a document workflow: PENDING->{ACTIVE,
SUSPENDED,TERMINATED}; ACTIVE->{SUSPENDED,TERMINATED}; SUSPENDED->{ACTIVE,
TERMINATED}; TERMINATED->{ARCHIVED}; ARCHIVED terminal. ACTIVE<->SUSPENDED
is the only real cycle (an employee can be suspended and reactivated more
than once over their employment); there is no single "done" milestone the
way License's EXPIRED or Quote's ACCEPTED is -- PENDING->ACTIVE is simply
onboarding completing, and TERMINATED->ARCHIVED is a records-retention
action, not a business "success" state. Forcing this into
components/commercial_record.html's steps() linear-progress-bar macro
would misrepresent the real lifecycle the same way it would for
Subscription/Installation -- see
docs/owner/ui-modernization/licensing-command-center-contract.md's own
"Timeline-visualization decision" for the same reasoning applied there.
employees/detail.html already shows a real, already-persisted, per-change
"Audit timeline" (AuditLog rows filtered to
entity_type="employee_profile", app.employees.routes.detail()) -- the
honest, existing chronological record, kept exactly as-is; no new timeline
component was built for this pass.

Badge-color mapping is centralized here for the same reason every prior
Stage D pass centralized its own: employees/list.html's and
employees/detail.html's existing inline ternary (`'active' if status ==
'ACTIVE' else ('warn' if status in ('PENDING','SUSPENDED') else 'danger')`)
was verified against app.models.employees.EMPLOYMENT_STATUSES (`PENDING`,
`ACTIVE`, `SUSPENDED`, `TERMINATED`, `ARCHIVED` -- exactly 5, exactly what
employees/list.html's own status filter dropdown already enumerates) --
every one of the 5 real statuses is already covered with no missing
branch: TERMINATED/ARCHIVED both fall into the real 'danger' bucket,
PENDING/SUSPENDED into 'warn', ACTIVE alone is 'active'. Reproduced
byte-for-byte here, not changed -- there is no disclosed badge-color gap
to fix on this screen (unlike Subscription/Installation/License in Stage
D.5, which each had a real missing-danger-branch bug). The literal class
names 'active'/'warn'/'danger' (not 'success'/'pending'/'danger') are kept
exactly as the pre-existing templates always used them -- components.css
maps both vocabularies to the identical three color buckets
(`.badge.active,.badge.confirmed,.badge.success,.badge.ok` /
`.badge.warn,.badge.pending,.badge.draft` /
`.badge.danger,.badge.revoked,.badge.failed,.badge.disabled`), so there is
no visual difference, only a naming one this module deliberately does not
disturb.

The presence badge (`ONLINE`/`RECENTLY_ACTIVE`/`OFFLINE`, a real-time
signal computed by app.employees.presence, not a persisted status) gets
its own small badge-class helper for the same centralization reason --
employees/list.html and employees/detail.html each carried their own
identical copy of the same ternary before this pass.
"""
from __future__ import annotations


def employment_status_badge_class(status: str) -> str:
    if status == "ACTIVE":
        return "active"
    if status in ("PENDING", "SUSPENDED"):
        return "warn"
    return "danger"


def presence_badge_class(presence: str) -> str:
    if presence == "ONLINE":
        return "active"
    if presence == "RECENTLY_ACTIVE":
        return "warn"
    return ""
