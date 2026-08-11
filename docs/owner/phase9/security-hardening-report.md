# Phase 9 Milestone 10 — Security Hardening Report

## Owner web

| Control | Status | Evidence |
|---|---|---|
| Secure/HttpOnly/SameSite cookies | PASS | `BaseConfig`: `SESSION_COOKIE_HTTPONLY=True`, `SESSION_COOKIE_SAMESITE="Lax"`, `SESSION_COOKIE_SECURE` true in staging/production (Phase 6/8, unchanged) |
| CSRF protection | PASS | `CSRFProtect` (`flask-wtf`) initialized on every route except the explicitly-exempted, independently-authenticated (Ed25519-signed) external licensing API — documented exemption, not a blanket weakening |
| Login rate limits | PASS | `LOGIN_MAX_ATTEMPTS=5`, `LOGIN_LOCKOUT_SECONDS=900` (Phase 4/8, unchanged) |
| MFA enforcement | PASS | Super Admin `mfa_required` enforced; `super_admin_mfa_required` preflight/readiness check (Phase 8V-P9, reused) |
| Recent-auth enforcement | PASS | `RECENT_AUTH_WINDOW_SECONDS=600` for sensitive actions (Phase 6/8, unchanged) |
| Session expiry | PASS | `PERMANENT_SESSION_LIFETIME_SECONDS=28800` (8h absolute), `SESSION_IDLE_TIMEOUT_SECONDS=1800` (30m idle) |
| Logout invalidation | PASS (not re-verified this session, no code changed) | Existing Phase 4/8 behavior |
| Clickjacking protection | PASS | `X-Frame-Options: DENY` (real, tested this phase — `test_security_headers.py`) |
| Template escaping | PASS | Jinja2 default autoescape, not disabled anywhere in `owner/app/templates` |
| **HSTS environment gap** | **FIXED this phase** | Was production-only; staging now correctly included (`tls-and-security-headers.md`) |

## External API

| Control | Status | Evidence |
|---|---|---|
| Request size limits | PASS | `MAX_REQUEST_BYTES=65536` / `MAX_CONTENT_LENGTH` |
| Nonce/replay protection | PASS | `REPLAY_PROTECTION_REQUIRED=True`, enforced fail-closed by `validate_external_api_production()` |
| Timestamp bounds | PASS | `ACTIVATION_TIMESTAMP_SKEW_SECONDS=300` |
| Request signatures | PASS | Ed25519 device-signed requests (Phase 6/8) |
| **Stale/replay assertion rejection** | PASS | Phase 8V-P9's real monotonicity fix, physically proven, unchanged this phase |
| No CORS overexposure | PASS | `CORS(app, resources={r"/api/*": {"origins": []}})` — zero origins permitted by default |
| No license-key retransmission | PASS | Confirmed structurally in Phase 8V-P7/P9 wire-capture evidence, unchanged |
| No customer-domain data | PASS | Structural — see `network-and-trust-boundaries.md` |

## Database

| Control | Status | Evidence |
|---|---|---|
| Parameterized queries | PASS | SQLAlchemy ORM/Core `text()` with bound params throughout; no raw string-interpolated SQL found in `owner/app` |
| Least privilege | PARTIAL | Real staging DB is separate; dedicated least-privilege role NOT created this session (no superuser access) — see `postgresql-hardening.md` |
| No public listener | PASS (Compose-level) | `docker-compose.staging.yml`'s `db` service has no `ports:` mapping |

## Infrastructure

| Control | Status | Evidence |
|---|---|---|
| No default credentials | PASS | `BaseConfig.validate()` fails closed with no default secret in staging/production |
| No development server in staging | PASS | `owner/Dockerfile.staging` runs Gunicorn, not `flask run` |
| No debug mode in staging | PASS | `StagingConfig.DEBUG = False` |
| No directory listing / exposed admin DB tools | PASS | Nothing in `docker-compose.staging.yml` exposes pgAdmin or a static file listing |
| No trust-all TLS | PASS | Caddy terminates real TLS; no code path in this repo disables certificate verification |

## Real scans run this session

- **`pip-audit`**: 32 real CVEs found across 7 dependencies, all remediated and regression-tested (see
  `dependency-risk-register.md`). Zero remaining.
- **`bandit`** (`owner/app`, recursive): 4 findings, **all LOW severity, zero HIGH/MEDIUM**. Manually
  reviewed: 1 is `owner/app/config.py`'s `TestingConfig.SECRET_KEY = "test-secret-key"` (a deliberate
  test-only value, not a real secret — false positive); 3 are `subprocess` usage in the pre-existing
  `owner/app/system/backup.py`, confirmed safe on inspection (list-argv, no `shell=True`, no untrusted
  input reaches the command — false positive, not a real vulnerability). Raw evidence:
  `docs/owner/phase9/evidence/bandit-owner-app.json`.
- **SBOM**: real CycloneDX 1.6 SBOM generated from the actual venv, 94 components. See
  `sbom-report.md`.

## No P0 or P1 remaining

Confirmed by the real scans above, all re-run after remediation.
