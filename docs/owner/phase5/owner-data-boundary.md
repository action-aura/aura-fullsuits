# Phase 5 -- Aura Owner Data Boundary

## The rule
Aura Owner stores **commercial and technical metadata about Action Aura's relationship with a customer organization** -- who bought what, what license they hold, what devices are registered. It never stores what that customer *does* with the product (their sales, patients, inventory, medical records).

## Forbidden data (never has a column, model, or serialized field in Owner)
Retail: sale/invoice line items, inventory quantities/stock levels, product cost/price data belonging to a customer's catalog, the customer's own end-customer records.
Clinic: patient identity/demographics, appointments, prescriptions, diagnoses, medical/clinical notes, clinic invoices or payment amounts for patient care.
Universal: card/bank credentials, local database file paths or contents, precise geolocation, full raw hardware identifiers (IMEI, full MAC).

## Allowed data (exhaustive per Part P/owner-platform-entry-decision.md)
Staff identity/roles; product/platform/version/plan/price/add-on/entitlement catalog (Owner's own commercial catalog, not the product's internal data); customer organization/contact metadata; subscription/renewal/payment records (Owner's own commercial bookkeeping, explicitly not accounting revenue); license records and license-key hashes; installation/device labels and status; activation-event records; Owner's own audit log.

## Enforcement mechanism (not just documentation -- see `forbidden-data-enforcement-report.md`)
1. **Structural**: no SQLAlchemy model in `owner/app/models/` has a column for any forbidden field; no import path exists from `owner/` into `products/*` or `commercial_runtime/*`.
2. **Allowlist serializers**: every future external contract (Part R) is built as an explicit allowlist (only named fields are ever serialized), not a blocklist and not `**dict(model.__dict__)`.
3. **Automated forbidden-field guard**: `owner/tests/test_data_boundary.py` asserts, for every allowlist serializer and every SQLAlchemy model, that none of a fixed forbidden-term list (`patient`, `diagnosis`, `prescription`, `medical_note`, `sale_line`, `invoice_line`, `inventory_quantity`, `customer_purchase`, `local_database`, `card_number`, ...) appears as a column/field name, and that constructing a serializer payload containing one of these keys raises.

## Classification reference
See `owner-data-classification.md` (Part W) for the full allowed/forbidden field inventory and `product-to-owner-data-allowlist.md` for the exact allowlist used by each future contract schema.
