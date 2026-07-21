# Phase 6 -- External API Security Report (Part X)

| Control | Implementation | Evidence |
|---|---|---|
| Strict JSON content type | `@bp.before_request` rejects any POST without `Content-Type: application/json` (415) | `test_wrong_content_type_rejected` |
| Payload size limit | `OWNER_MAX_REQUEST_BYTES` (default 64KB) via Flask `MAX_CONTENT_LENGTH`, `@bp.errorhandler(413)` | config.py, routes.py |
| Canonical request validation | Every signature verification re-derives canonical bytes server-side; never trusts client-claimed ordering | canonical.py |
| Signature verification | Ed25519, `cryptography` library, real key material, every activation/check-in/deactivation | 18 crypto tests + live simulator run |
| Constant-time secret comparison | License lookup is by HMAC-indexed equality (unique DB index), not a linear compare of plaintext -- inherits Phase 5's `hmac.compare_digest` discipline in `license_keys.py` | `security/license_keys.py` (unchanged) |
| Request-body secret redaction | The plaintext key never reaches any log statement; `del`eted from scope immediately after use | activation.py |
| Replay protection | Postgres nonce store, fail-closed | replay-protection-design.md |
| Distributed rate limiting | Postgres counters, fail-closed | distributed-rate-limiting-design.md |
| Idempotency | Postgres-backed, cross-process correct | idempotency-design.md |
| Secure error responses | Fixed allowlist response shape, no internal exception text ever included (`except Exception: ... logger.exception(...); return _error_response("INTERNAL_DECISION_FAILURE", 500, ...)`) | routes.py |
| Correlation IDs | Every response echoes the caller's `correlation_id` | routes.py |
| No stack traces | `INTERNAL_DECISION_FAILURE` response never contains exception text; logged server-side only | routes.py |
| No debug mode | Same `DEBUG=False` production discipline as Phase 5 | config.py |
| No CORS wildcard | `CORS(app, resources={r"/api/*": {"origins": []}})` -- unchanged from Phase 5, covers `/api/licensing/v1/*` too | app/__init__.py |
| Explicit allowed methods | Each route declares exactly the HTTP methods it accepts | routes.py |
| Security headers | Inherited from Phase 5's global `register_security_headers` (CSP, X-Content-Type-Options, X-Frame-Options, etc.) -- applies to every response including this blueprint's | security/headers.py |
| Dependency scanning | `pip-audit` run this phase, findings classified | dependency-security-scan.md |
| External API disabled by default | Blueprint not imported unless `OWNER_EXTERNAL_API_ENABLED=true`; verified on the real config classes | test_data_boundary.py |
| Startup configuration validation | `validate_external_api_production()` refuses to start with a default/missing pepper, missing signing-key directory, replay/rate-limit protection disabled, debug mode, or insecure cookies, when the external API is enabled outside development | config.py |
| Safe structured logs | No request body ever logged wholesale; only reason codes and safe IDs | activation.py, checkin.py, deactivation.py |
| Log injection resistance | All logged values are UUIDs/enum-like reason codes, never free-form user input | (structural -- no free-text field is ever interpolated into a log line) |
| Database transaction rollback | Every route's exception handler calls `db_session.rollback()` before building the error response | routes.py |
| Concurrency protection | `SELECT ... FOR UPDATE` row lock, proven under real threads | device-limit-concurrency-report.md |
| Anti-enumeration | Internal-only reason codes normalized publicly | external-api-reason-code-catalog.md |
| Audit-chain continuity | Verified intact after real Phase 6 activity | test_phase6_audit.py |
| Privilege checks (admin UI) | Every `/licensing-admin/*` route requires a specific permission + recent-auth for mutations | licensing_admin/routes.py |
| MFA/recent-auth for cryptographic admin ops | Signing-key generate/activate/rotate/revoke, device-key revoke, offline-policy assignment all require `@require_recent_auth` | licensing_admin/routes.py |

## Denial-of-service considerations
Rate limiting bounds per-source request volume; `OWNER_MAX_REQUEST_BYTES` bounds per-request cost; the nonce/rate-limit tables have indexed, bounded lookups (no full-table scans on the hot path). A dedicated infrastructure-level DoS defense (e.g. a reverse-proxy rate limiter, connection limits) is out of scope for this phase's localhost-only deployment model.

## Not addressed this phase (disclosed)
Distributed rate limiting across truly independent Owner *deployments* (only across workers/processes of one deployment); a license-key-hash-derived rate-limit bucket dimension (IP-only today); CI-integrated `pip-audit` (no CI exists for Owner yet, Phase 5 residual risk, not resolved by Phase 6).
