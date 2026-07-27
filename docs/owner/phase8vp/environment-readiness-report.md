# Phase 8V-P — Environment Readiness Report (Part A/D)

## Versions and identifiers, recorded fresh this session

| Item | Value |
|---|---|
| Owner Alembic schema revision (head) | `0f8d55b753ed` (unchanged since Phase 8 Milestone 5 — no new migration this phase) |
| Commercial contract version | `v1` (`commercial_runtime/licensing_contracts/client.py::CONTRACT_VERSION`) |
| Assertion version | `1` (`owner/app/licensing_service/assertions.py::ASSERTION_VERSION`) |
| Local licensing schema version | `1` (`commercial_runtime/licensing_contracts/state_repository.py::LICENSING_SCHEMA_VERSION`, unchanged) |
| Backup schema version | `1` (`commercial_runtime/backup/service.py::SCHEMA_VERSION`, unchanged) |
| PostgreSQL | `17.10` |
| Java | OpenJDK `17.0.19` (Microsoft build) |
| Android SDK | present at `C:\Users\Dell\AppData\Local\Android\Sdk`, no device attached |

## A real infrastructure gap found and fixed before any product build

The repository's canonical `commercial_runtime/licensing_contracts/trust_anchor.json` (bundled into
every product build as the trust-on-first-use seed) referenced signing key
`owner-ed25519-20260724T205903Z-ca469aa3`. The persistent Owner dev database
(`aura_owner_dev`) has **zero** rows in `owner_signing_keys` — that key no longer exists anywhere in
this environment (generated in some earlier, now-gone session state). A product built against the
old trust anchor would reject every real assertion Owner could actually issue today —
`INVALID_SIGNATURE`/`SIGNING_KEY_UNAVAILABLE` on the very first activation attempt.

Fixed the only correct way: generated and activated a **new** real Ed25519 signing key in the actual
persistent dev database (`owner-ed25519-20260727T053324Z-c32537d7`), then updated
`trust_anchor.json` to embed its real public key. `commercial_runtime`'s 214-test suite reconfirmed
green (no test hardcodes the old key). This is exactly the kind of "real P0 blocks validation"
correction this phase's own rules allow — without it, no real Windows product build could complete
even the very first activation this session set out to validate.

## Owner external API URL — verified correct, not assumed

`products/clinic/backend/config.py`/`products/retail/backend/config.py`'s
`OWNER_LICENSING_BASE_URL` is deployment-time configuration (`AURA_OWNER_LICENSING_URL` env var, no
hardcoded default) consumed by `commercial_runtime.licensing_contracts.routes.py`'s blueprint, whose
`url_prefix` is `/api/licensing` — the client itself (`LicensingClientConfig.base_url`) must be set to
the FULL `.../api/licensing/v1` path (Owner's external blueprint prefix, confirmed by reading
`owner/app/api_external/routes.py::bp = Blueprint(..., url_prefix="/api/licensing/v1")`). Every
real product run this session sets `AURA_OWNER_LICENSING_URL` to the complete path explicitly —
verified by a successful `/service-info`/`/signing-keys` call before any scenario work began (see
`validation-environment.md`), not assumed correct from configuration alone.
