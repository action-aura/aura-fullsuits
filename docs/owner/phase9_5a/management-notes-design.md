# Phase 9.5A Milestone 14 — Management Shared Notes Design

Genuinely separate from `CustomerNote` (see `duplication-risk-report.md`) — never customer-scoped.

## New: `shared_management_notes` (`owner/app/models/management_notes.py`)

```
id, title, body, category (bounded string, e.g. OPERATIONS|HR|SALES_STRATEGY|GENERAL),
priority (LOW|MEDIUM|HIGH), status (OPEN|IN_PROGRESS|DONE|ARCHIVED, default OPEN),
pinned (boolean, default false), created_by_staff_user_id, updated_by_staff_user_id,
assigned_employee_profile_id (nullable), visibility (MANAGEMENT_ONLY|SPECIFIC_EMPLOYEES|ALL_STAFF,
  default MANAGEMENT_ONLY), due_date (nullable), created_at, updated_at, version, archived_at

management_note_visibility_grants: id, management_note_id (FK), employee_profile_id (FK) — only
  populated when visibility == SPECIFIC_EMPLOYEES; ignored otherwise.

management_note_comments: id, management_note_id (FK), author_staff_user_id, body, created_at
```

## Requirements

- Bahaa and Awab (both `SUPER_ADMIN`) see the same notes — no per-account filtering exists in the
  query for `MANAGEMENT_ONLY`/`ALL_STAFF` visibility; `visibility.manage`-equivalent access is simply
  "any `SUPER_ADMIN`," not an allowlist of specific accounts.
- Actions remain individually attributed — `created_by_staff_user_id`/`updated_by_staff_user_id` are
  always the real acting account, plus a `MANAGEMENT_NOTE_CREATED`/`_UPDATED` audit event
  (Milestone 23) — shared visibility never means shared identity.
- Employees cannot access `MANAGEMENT_ONLY` notes — enforced the same way as Milestone 7's ownership
  filter: the list/detail query excludes `MANAGEMENT_ONLY` notes entirely for a non-management caller,
  includes `SPECIFIC_EMPLOYEES` notes only when a matching `management_note_visibility_grants` row
  exists for that employee, and includes `ALL_STAFF` notes for everyone.
- Optimistic locking (`version` column) — a `VERSION_CONFLICT` error (Milestone 19) prevents Bahaa and
  Awab silently overwriting each other's concurrent edit to the same note.
- Archived notes remain auditable — `archived_at` set, row retained, never hard-deleted.
