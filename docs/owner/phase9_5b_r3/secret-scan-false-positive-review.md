# Phase 9.5B-R3 — Secret Scan False-Positive Review

72 findings across 57 git-tracked files, individually reviewed. No blanket
allowlist entry was used — each finding was checked against its real file
content before disposition. Grouped by category with exact paths.

## 1. Dev-only placeholder secrets (self-documenting, 3 findings)

- `owner/app/config.py:117` — `SECRET_KEY` fallback literally named
  `"dev-only-insecure-key-do-not-use-in-production"`.
- `owner/app/config.py:129` — `LICENSE_PEPPER` fallback, same pattern.
- `owner/docker-compose.yml:10` — same dev-only `SECRET_KEY` default,
  referenced for local `docker compose up`.

Not a secret: the value's own text is a warning label, not a credential;
production deployment requires the real environment variable to be set.

## 2. Dev database connection strings (14 findings)

`owner/.env.example:10`, `owner/alembic.ini:5`, `owner/app/config.py:117,129`,
`owner/docker-compose.yml:30`, `owner/README.md:39`, `owner/tests/conftest.py:12`,
and 7 docs files under `docs/owner/phase5/`, `docs/owner/phase6/`,
`docs/owner/phase8v*/`, `docs/licensing/phase7/`, `docs/corrections/launcher/`.

All are the same `postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/...`
pattern — a `localhost`-only development credential, established and
documented as such since this project's earliest phases (Phase 5). Never a
production or remote credential.

## 3. Test fixture passwords/tokens (real synthetic-only, ~30 findings)

`owner/tests/conftest.py:83`, `test_auth.py:16,31,143`, `test_rbac.py:7`,
`test_security.py:85,115`, `test_phase6_security_controls.py:96`,
`test_phase9_5b_*.py` (3 files), plus Retail/Clinic equivalents
(`retail_*_test.py` ×7, `clinic_*_test.py` ×4,
`commercial_runtime/licensing_contracts/tests/test_internal_sync_routes.py`).

All are synthetic literal strings (e.g. `"Sup3r-Str0ng-Pass!"`, fake TOTP
seeds, fake bearer tokens) used exclusively to exercise each product's own
authentication/authorization test coverage. None correspond to any real
credential, environment, or deployed system.

## 4. Redaction-test assertions (1 finding, `Private Key` type)

`owner/tests/test_observability_logging.py:30` —
`test_redacts_pem_private_key_block()` asserts the log-redaction function
strips a **truncated, non-functional** PEM fragment
(`MIIEvQIBADANBgkqhkiG9w0BAQEFAASCB`, 33 characters — real PEM bodies are
hundreds of characters). Verified by reading the test body directly: this
is the security-control test, not a leaked key.

## 5. Algorithm/charset constants (1 finding)

`owner/app/security/license_keys.py:18` — `_ALPHABET =
"23456789ABCDEFGHJKMNPQRSTUVWXYZ"`, a documented Crockford-style base32
symbol set (see the file's own module docstring), flagged purely by
entropy heuristic. Not a secret value.

## 6. Migration revision hashes (7 findings)

`owner/migrations/versions/{0b1d294dfb40,0f8d55b753ed,338d06dece44,
3c0d51d82d8c,60f363ee66e8,62e4adb0a7b9,8646da2df010,af7831a6dc4d}_*.py` —
Alembic's own auto-generated revision-ID hex strings (structural
identifiers for migration ordering), not secrets.

## 7. Historical evidence/report artifacts (remaining findings)

`docs/audit/22-master-defect-registry.json`,
`docs/owner/phase8v/real-traffic-evidence.md`,
`docs/owner/phase8vp5/raw-wire-evidence.md`,
`docs/owner/phase9/evidence/bandit-owner-app.json`,
`docs/owner/phase9/security-hardening-report.md`,
`docs/owner/phase9_5b_r/auth-and-mfa-localization-report.md`,
`docs/release/android-production-signing-policy.md`,
`docs/migration/source-inventory.md`,
`products/{clinic,retail}/frontend/locales/en.json` (the literal string
"secret" appearing in a UI label's English text, e.g. a settings-page
label — not a credential) — hex/base64 correlation IDs, hashes, and scan
output from prior phases' own real evidence-gathering, or plain UI text
containing the word "secret"/"key". None are live credentials.

## Allowlist decision

No `detect-secrets` baseline/allowlist file was added to the repository.
Given every finding is a real, individually-justified false positive
(documented above) rather than a recurring noisy pattern needing
permanent suppression, and given the governing spec's explicit caution
against a "broad baseline allowlist" hiding true findings, this review
document itself serves as the disposition record instead.
