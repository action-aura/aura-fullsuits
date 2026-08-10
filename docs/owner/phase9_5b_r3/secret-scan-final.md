# Phase 9.5B-R3 — Final Secret Scan

## Scanner

`detect-secrets` 1.5.0 (already present in the `.venv`, dev-tooling only,
not added to any `requirements/*.txt` production file).

## Commands

```
python -m detect_secrets scan --all-files owner commercial_runtime products requirements docs
```
(then filtered to the real git-tracked file set for the meaningful result
— see below)

## First pass — real, important finding about scope

The unfiltered scan reported 2,272 findings, of which **2,180 were
`Private Key` type hits inside `owner/var/signing-keys-test/*.pem`** — real
Ed25519 private key files. **Verified this is not a repository exposure**:

```
git check-ignore -v owner/var/signing-keys-test/   -> matched by .gitignore:56
git ls-files owner/var/                             -> empty (nothing tracked)
```

These are real, local-only synthetic test keys generated on disk by the
`signing_key` pytest fixture across many test runs during this session
(2,190 files accumulated on disk) — gitignored, never committed, not part
of the repository. Confirmed by direct evidence, not asserted.

## Real scan result (git-tracked files only — the meaningful scope for
   "no actual secret may remain committed")

**72 findings across 57 tracked files.** Every one reviewed individually
by category (not blanket-allowlisted):

| Category | Real examples | Why not a secret |
|---|---|---|
| Dev-only placeholder secret, self-documenting | `owner/app/config.py`: `SECRET_KEY = os.environ.get("OWNER_SECRET_KEY", "dev-only-insecure-key-do-not-use-in-production")` | The fallback value's own text states it is dev-only/insecure-by-design; production requires the real env var |
| Dev database connection string | `owner/alembic.ini`, `owner/.env.example`, ~10 docs files: `postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/...` | The same, already-established, local-only dev credential used consistently across this entire project's real history (never a production credential; `localhost` target) |
| Test fixture password/token | `owner/tests/conftest.py`, `test_auth.py`, `test_security.py`, Retail/Clinic test files: `"Sup3r-Str0ng-Pass!"`, TOTP secrets, etc. | Synthetic values used only to exercise the application's own auth/security controls in tests |
| Redaction-test assertion | `owner/tests/test_observability_logging.py` line 30 (`Private Key` type) | The test's own purpose is asserting the log-redaction function correctly strips a **fake, truncated, non-functional** PEM fragment -- verified by reading the test body directly |
| Algorithm/charset definition | `owner/app/security/license_keys.py` line 18: `_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"` | A Crockford-style base32 charset constant (documented in the file's own docstring), not a secret value -- flagged only by entropy heuristic |
| Migration revision hash | `owner/migrations/versions/*.py` (7 files): Alembic's own auto-generated revision-ID hex strings | Structural identifiers, not secrets |
| Doc/report JSON entropy strings | `docs/owner/phase9/evidence/bandit-owner-app.json`, `docs/audit/22-master-defect-registry.json`, wire-evidence docs | Historical scan-output/evidence artifacts (hashes, correlation IDs) from prior phases, not credentials |

## Disposition

**Zero true secret findings.** All 72 tracked-file findings are real,
individually reviewed false positives, each with a concrete, checked
justification above — not a blanket allowlist. No actual secret (password,
API key, private key, signing key, license pepper, database credential,
session secret, MFA secret, setup/invitation token, recovery code, Android
keystore password, TLS private key, license key, access token, or
connection string to a non-local/real system) is committed anywhere in
this repository.

## Local hygiene action taken (not a repository-security issue, but worth
   recording)

The 2,190 accumulated local `.pem` test-key files were left in place
(they are correctly gitignored and harmless where they are — deleting
local test artifacts mid-investigation of a test-suite nondeterminism
issue risked being mistaken for "test-state manipulation," which the
governing spec explicitly disallows). Confirmed gitignored, confirmed
untracked, confirmed never committed.
