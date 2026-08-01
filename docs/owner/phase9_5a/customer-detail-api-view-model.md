# Phase 9.5A Milestone 9 — Customer/Lead Detail API View Model

## Expanded detail response (role-safe field filtering applied server-side)

```json
{
  "id": "uuid", "record_type": "CUSTOMER",
  "identity": {"legal_name": "...", "contacts": [...], "addresses": [...]},
  "status": "ACTIVE", "source": "REFERRAL",
  "assigned_employee": {...}, "created_by_employee": {...},
  "locations": [{"id": "uuid", "latitude": "...", "verified": false, "captured_at": "..."}],
  "interactions": [{"type": "CALL", "summary": "...", "occurred_at": "...", "employee": {...}}],
  "notes": [{"body": "...", "author": {...}, "created_at": "...", "management_only": false}],
  "followups": [{"due_at": "...", "completed_at": null}],
  "product_interests": ["AURA_RETAIL"],
  "commercial": {
    "quotes": [{"id": "uuid", "status": "SENT", "total": {"amount": "...", "currency": "..."}}],
    "orders": [...], "invoices": [...], "confirmed_payments": [...]
  },
  "licensing": {
    "subscription": {"id": "uuid", "status": "ACTIVE", "device_allowance": 2},
    "license": {"id": "uuid", "device_limit": 2, "status": "ISSUED"},
    "installations": [{"id": "uuid", "platform": "WINDOWS", "status": "ACTIVE"}]
  },
  "commission_attribution": [{"employee": {...}, "status": "PAID"}],
  "status_history": [...], "assignment_history": [...],
  "attachments": [], "audit_references": null,
  "version": 4
}
```

## Role-safe field filtering (server-computed, not client-hidden)

`commercial`/`licensing`/`commission_attribution` blocks are omitted entirely (not present as empty/
null — absent from the JSON) for a caller without the corresponding view permission
(`invoices.view`-equivalent read access, `licenses.view`, `commissions.view_all`). `audit_references`
is always `null` for non-management callers (a placeholder field reserved for a future
audit-cross-reference feature, not yet populated for anyone this phase). `notes` filters out any note
explicitly flagged `management_only: true` before the response is built, not after.

## Summary endpoints avoid loading full detail for every card

The card view model (Milestone 9's collapsed shape) is a genuinely separate, cheaper query — never
"call the detail endpoint N times and truncate," which is exactly the N+1-at-the-endpoint-level anti-
pattern the card contract's own "no N+1" requirement guards against.

## Bounded response size

`interactions`/`status_history`/`assignment_history` arrays are capped (e.g. most recent 20, with a
separate paginated sub-endpoint for full history) — the detail endpoint is not an unbounded firehose
for a customer with years of history.
