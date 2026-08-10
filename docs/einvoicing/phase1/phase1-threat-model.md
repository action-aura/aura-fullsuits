# Phase 1 threat model

## Assets

- JoFotara `client_id`/`client_secret` (per install).
- Submitted invoice documents (`einvoice_outbox.document_xml`) — business
  data already present elsewhere in the product DB.
- The submission audit trail.
- The gapless per-company sequence number itself (a compliance artifact —
  a gap or duplicate is a finding against the business, not just a bug).

## Threats and mitigations

| Threat | Mitigation |
|---|---|
| Credential theft from disk | Encrypted at rest (DPAPI on Windows, AES-GCM/HKDF on Android). Never logged, never in the DB, never echoed by any HTTP response — `credential-storage-design.md`. |
| Double submission of the same invoice (duplicate tax filing) | Four independent layers — UNIQUE index, conditional claim, pre-call timestamp, never-auto-resubmit-on-UNKNOWN — `outbox-state-machine.md`. |
| Feature silently changing existing customers' behavior | Default OFF; regression suites pin the exact response shape, table schema, and thread count of the disabled path; migration is additive-only. |
| Feature blocking or slowing checkout | Enqueue is one local SQLite write, no network, wrapped in a broad try/except; submission happens entirely in a background worker. Proven under a scripted 5-second-latency mock provider in `retail_einvoicing_test.py`/`clinic_einvoicing_test.py` — the sale response returns in well under 2 seconds regardless. |
| Runaway retry hammering the tax authority | Exponential backoff with jitter, capped at 1 hour; hard `max_attempts` → `FAILED_PERMANENT`, never infinite. Rate-limited within a single worker pass. |
| Secret or business data leaking into logs | `FORBIDDEN_DETAIL_MARKERS` guard on every audit event, raises rather than redacts (a caller must fix the call site). No raw exception text ever returned to an HTTP client — sanitized `reason_code` + generic message only, matching `SECURITY.md`'s existing rule. |
| Customer business data reaching the Owner Control Center | No code path exists — `product-to-owner-data-boundary-einvoicing.md`. |
| A future contributor fabricating real-looking ISTD API details before real docs exist | `providers/direct_istd.py` raises `NotImplementedError` everywhere; source-scan tests assert no URL literal, no HTTP call, no plausible-looking field constant. |
| Cross-company data leakage (multi-tenant registry) | Every route and repository method takes `company_id` from the server-side session (`_cid()`), never a client-supplied value — same pattern already used by `retail_api.py`/`clinic_api.py`. `test_outbox_repository.py::test_counts_by_state_scoped_per_company` and the worker registry tests (`test_two_companies_get_independent_workers`) verify isolation directly. |

## Explicitly out of scope for Phase 1

- Real ISTD traffic (no live credentials exist yet — Phase 2).
- Android credential entry / native UI (see `phase1-residual-risk-register.md`).
- Fine-grained per-role capability gating on settings/credential writes
  (`settings_capability`/`capability_guard` params exist in `routes.py` but
  are unused in Phase 1 — admin-session gating only).
