# Phase 5 -- Owner API Contract Preparation (Part R)

## Deliverable
7 JSON Schema (draft 2020-12) files under `owner/contracts/`, each `"additionalProperties": false` and matching `product-to-owner-data-allowlist.md` exactly:
`activation-request-v1.schema.json`, `activation-response-v1.schema.json`, `license-check-request-v1.schema.json`, `license-check-response-v1.schema.json`, `entitlement-response-v1.schema.json`, `installation-registration-v1.schema.json`, `product-version-check-v1.schema.json`.

## Status: inactive specifications, with one narrow live exception
Per the spec's explicit instruction, these are **specifications only** -- no live activation/license-check/entitlement endpoint exists. The one exception, also explicit in the spec ("Any prototype external route must remain disabled by default behind `OWNER_EXTERNAL_API_ENABLED=false`"): `owner/app/api/routes.py` implements a read-only `GET /api/v1/product-version-check` (matching `product-version-check-v1.schema.json`'s response shape via `serialize_product_version_check_response`) and a stub `POST /api/v1/installation-registration` that validates its request shape and returns `501 not_implemented_in_phase_5` -- deliberately not wired to any real registration logic yet.

## The disabled-by-default proof
`app/__init__.py`'s `create_app()` only does `from app.api.routes import bp as external_api_bp; app.register_blueprint(...)` **inside** an `if app.config.get("EXTERNAL_API_ENABLED"):` block -- when disabled (the default), the import never executes and the blueprint's routes are never added to `app.url_map` at all. This is stronger than a runtime check inside each view function: there is no route for a request to even match against. Verified: `owner/tests/test_data_boundary.py::test_external_api_blueprint_not_registered_by_default` asserts zero `/api/v1/*` rules exist in `app.url_map` under the default test config.

## Seam for Phase 6/7
Both live and future routes call into the same plain-Python service layer (`app/licensing/services.py`, `app/installations/services.py`) that the staff-facing UI also calls -- per `owner-domain-map.md`'s API-first design note, a future real activation endpoint is additive route wiring, not a rewrite of business logic.
