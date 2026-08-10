"""Jordan JoFotara (ISTD) e-invoicing -- shared product-side domain.

Imported by both Retail and Clinic Windows backends directly, and by both
Android apps' embedded Chaquopy backend (commercial_runtime is already staged
into the APK), mirroring commercial_runtime/licensing_contracts's existing
pattern -- see that package's __init__.py.

Nothing in this package imports from `products/`, and nothing in it ever
contacts the Owner Control Center -- see
docs/einvoicing/phase1/product-to-owner-data-boundary-einvoicing.md. All
per-install state (credentials, settings, the submission outbox) lives in
each product's own local database and app-data directory, never synced
anywhere else, matching this repo's existing privacy boundary (PRIVACY.md).

Default OFF. See docs/einvoicing/phase1/jofotara-integration-architecture.md
for the full design and docs/einvoicing/phase1/phase1-implementation-plan.md
for how this was built.
"""
