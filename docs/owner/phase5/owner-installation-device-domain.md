# Phase 5 -- Owner Installation/Device Domain (Part P/Q)

## Scope this phase
Installation and device records are created **manually by staff** through the Owner UI/service layer this phase -- there is no live product-side registration client. `owner_installations` links a `License` to a `Customer`/`Product`/`Platform`, tracks `status` through an explicit lifecycle (`owner-lifecycle-rules.md`), and records `first_registered_at`/`last_check_in_at`/`activation_count` as placeholders for future live check-in wiring.

## No raw hardware identifiers (Part P, explicit spec instruction)
`Installation`/`DeviceRecord` store `installation_label`, `device_label`, `fingerprint_hash` (a privacy-safe hash placeholder), and `device_public_key` (a future public-key-identity placeholder) -- there is no column for IMEI, full MAC address, or geolocation anywhere in the schema. Verified structurally: `owner/tests/test_installations.py::test_register_installation_no_raw_hardware_id_columns` directly inspects `Installation.__table__.columns` and asserts none of `imei`/`mac_address`/`geolocation` exist.

## Activation events (Part Q)
`owner_activation_events` records a fixed, closed set of 12 event types (`DEVICE_REGISTERED`, `ACTIVATION_REQUESTED/APPROVED/REJECTED`, `DEVICE_REPLACED`, `LICENSE_SUSPENDED/REACTIVATED/EXPIRED/REVOKED`, `CHECK_IN_RECORDED`, `OFFLINE_GRACE_STARTED/ENDED`) via `record_activation_event()`, which validates the event type against this closed set and raises on anything else. `register_installation()` automatically records a `DEVICE_REGISTERED` event -- verified by `owner/tests/test_installations.py::test_activation_event_recorded_on_registration`.

## Not connected to any live product
No route in this codebase receives a check-in, activation request, or telemetry ping from a running Retail/Clinic instance. The only path that creates these records is the manual Owner UI (`installations.register` permission, staff-driven). This matches the Owner-platform-entry-decision's explicit scope: "installation/device registration... may be created manually or through test harnesses only" in Phase 5.

## Future contract seam
`owner/contracts/installation-registration-v1.schema.json` and `activation-request-v1.schema.json`/`activation-response-v1.schema.json` define the inactive future shape a real product-side client would use; `app/installations/services.py`'s plain-function API (`register_installation`, `transition_installation`, `record_activation_event`) is the exact seam a future Phase 6/7 activation endpoint would call, per `owner-domain-map.md`'s API-first design note.
