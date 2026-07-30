# Phase 8V-P4 — On-Device Data Preservation

## Clinic

| Point in session | Patients visible |
|---|---|
| After first real activation (Scenario 1's installation) | 1 (`Synthetic Patient One`, created via real UI, blocked pre-activation with `LICENSE_INACTIVE`, succeeded post-activation) |
| After Scenario 1 renewal + check-in | 1 (unchanged) |
| After force-stop/reopen | 1 (unchanged) |

No patient record mutation observed at any point; the renewal, check-in, and restart operations
never touch product-domain tables (structurally guaranteed -- `apply_renewal_request()` and the
checkin route only ever write `Subscription`/`License`/`RenewalRequest`/`AuditLog`/local
`LicenseStateRecord` rows, never product-database rows, confirmed by source read this and prior
sessions).

## Retail

| Point in session | Products / Sales / Stock |
|---|---|
| After activation | 1 product (`Synthetic Product One`, $100.00, 20 in stock), 0 sales |
| After late renewal + check-in | 1 product, 20 in stock (unchanged by the renewal itself) |
| After the real post-renewal sale | 1 product, 19 in stock, 1 sale (`SALE-000001`, $100.00 collected) |
| After force-stop/reopen | Same installation ID/state confirmed (see `android-restart-persistence.md`); product screen not re-captured in this exact pass (session time constraint) |

Stock correctly decremented by exactly 1 unit for exactly 1 unit sold -- no double-deduction, no
partial mutation.

## Not exercised this session

Backup/restore/export were not triggered on-device this session (no button tapped, no file
produced) -- carried forward as unverified this pass, though the underlying backup/restore/export
mechanism is unchanged source and was proven real via the actual installed Windows products in
Phase 8V-P. The Retail 88.00 discount+tax combination specifically was not exercised (see
`scenario2-retail-late-renewal.md`).

## Result: **PASS** for every data point actually captured -- no unexpected loss, no historical-value
mutation, correct stock arithmetic for the one real sale performed.
