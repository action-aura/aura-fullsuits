# Phase 7 -- Product-to-Owner Data Boundary

## Everything a product ever sends to Owner, enumerated exhaustively

**Activation request** (once, or on explicit re-activation): `product_code`, `platform`, `app_version`, `release_channel`, `installation_id` (client-generated, pre-registration), `device_public_key`, `device_public_key_algorithm`, the full `license_key` (this call only), `idempotency_key`, `nonce`, `timestamp`, `request_id`, `correlation_id`, `signature`. This is the exact Phase 6 `activation-request-v1` schema -- Phase 7 does not add a single field to it. No installation label, hostname, username, IP address, hardware identifier, or business data is added.

**Check-in request**: `contract_version`, `request_id`, `correlation_id`, `timestamp`, `nonce`, `installation_id` (server-assigned), `signature`. Exactly the Phase 6 shape -- confirmed no `license_key` field exists in it at all (`activation-protocol-v1.md`). Phase 7 adds nothing.

**Deactivation request**: check-in fields plus `idempotency_key`. Same source, no additions.

**Key-set manifest fetch** (new in Phase 7, Part D): a plain `GET`, no request body beyond what an authenticated check-in already sends -- no new outbound data category.

## What is structurally impossible to send, not just policy-forbidden

The product-side `LicensingClient` (`commercial_runtime/licensing_contracts/client.py`) builds every outbound request body from a fixed, typed dataclass matching the schemas above -- there is no code path where a caller passes an arbitrary dict that could smuggle an extra field in. This mirrors Owner's own Phase 5/6 allowlist-guard pattern (`api/serializers.py::_guard`, `assertions.py::_guard_payload`) applied at the sender instead of the receiver: even if a future engineer tried to pass a patient name or a sale total into an activation/check-in call, the dataclass constructor would reject the unexpected keyword argument at the call site, not silently serialize it.

Explicitly and permanently absent from every request-building code path: patient records, appointments, prescriptions, clinical/medical notes, Clinic invoices/payments, Retail sales/inventory/stock/customer records, local database file paths or contents, hostnames, usernames, IP addresses, geolocation, hardware serial numbers, screenshots, or any local licensing *event* (Part W's local event log is written locally and is never transmitted to Owner by any Phase 7 code -- Part W's own text is explicit about this: "Do not silently transmit these local events to Owner").

## Everything a product ever receives from Owner

Activation/check-in/deactivation responses (Phase 6's exact shapes), the signed assertion envelope (whose payload allowlist is itself fixed by Owner's own `FORBIDDEN_ASSERTION_MARKERS` guard -- a second, independent layer of the same non-negotiable boundary), and the signed key-set manifest (Part D). Nothing else. The product never calls any other Owner endpoint, and no Phase 7 code introduces a generic "fetch arbitrary data from Owner" capability.

## Verification method (not just design intent)

A conformance test suite (`commercial_runtime/licensing_contracts/tests/test_data_boundary.py`, mirroring Owner's own `test_phase6_data_boundary.py` naming) asserts, for every outbound request the client can construct: the serialized JSON's key set is a subset of the schema's documented allowlist, checked against the actual `owner/contracts/*-v1.schema.json` files so client and server never silently drift apart. This is written and run as part of Part C, before any UI or activation flow is built on top of the client -- catching a boundary violation at the lowest possible layer, the same discipline Owner's own Phase 5/6 data-boundary tests already established.
