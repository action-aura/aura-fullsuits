# Phase 9.5E Milestone 26 — Secret Scan (Executed)

Real `detect-secrets scan --all-files` run on 2026-08-03 against every new/modified
Phase 9.5E file: application code (`app/expenses`, `app/cash_closing`,
`app/operational_reports`, `app/management_notes`, `app/operations_ui`,
`app/api_operations/expenses_and_operations.py`), all three Phase 9.5E
migrations, every `tests/test_phase9_5e_*.py` file, and (deliberately, since
they're shared infrastructure this phase relies on) `app/config.py` and
`tests/conftest.py`. `--all-files` bypasses the repo's existing
`.secrets.baseline` allowlist so this run re-examines everything from
scratch rather than trusting the prior baseline's exclusions. Raw JSON
preserved at `scratchpad/detect_secrets_9_5e_all.json`.

Without `--all-files` (i.e. respecting the existing baseline, the mode a real
pre-commit hook would run in) the same file set returns **zero** findings --
every one of the 8 findings below was already accepted into the baseline by
an earlier phase or by this one. `--all-files` was used here specifically to
independently re-verify each finding is still a genuine false positive, not
to rely on the baseline's own say-so.

## Findings (8 total, all reviewed individually, all false positives)

| # | File | Line | Type | What it actually is |
|---|---|---|---|---|
| 1 | `app/config.py` | 126 | Basic Auth Credentials | The well-known dev-only Postgres URL `postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/aura_owner_test` — the same literal used everywhere in this codebase since Phase 5, never a production credential |
| 2 | `app/config.py` | 138 | Secret Keyword | `SECRET_KEY = "test-secret-key"` in `TestConfig` — a literal placeholder, active only under `TESTING = True` |
| 3 | `tests/conftest.py` | 13 | Basic Auth Credentials | Same dev-only Postgres URL as #1 |
| 4 | `tests/conftest.py` | 110 | Secret Keyword | `make_staff()`'s default test-fixture password `"Sup3r-Str0ng-Pass!"` — a literal used to create synthetic test accounts, never a real credential |
| 5 | `tests/test_phase9_5e_dev_server_port_isolation.py` | 17 | Basic Auth Credentials | Same dev-only Postgres URL as #1, passed to the port-isolation harness for its own isolated test server |
| 6 | `tests/test_phase9_5e_security_and_idor.py` | 317 | Secret Keyword | `"SECRET-NOTE-BODY-MARKER-XYZ"` — a deliberately-named test marker string used to assert a management note's body is not leaked across a visibility boundary, not an actual secret |
| 7 | `migrations/versions/e8fb57c82a58_..._.py` | 13-14 | Hex High Entropy String | Alembic-generated `revision`/`down_revision` hash identifiers (`e8fb57c82a58`, `b2f6a8e13c74`) — routine migration bookkeeping, not key material |
| 8 | `migrations/versions/3f95d792998c_..._.py` | 13 | Hex High Entropy String | Alembic-generated `revision` hash identifier (`3f95d792998c`) — same as #7 |

## Disposition

Zero real secrets found. Every finding is either the same long-standing
dev-only database credential already accepted throughout the codebase, a
literal test/placeholder value, a deliberately-named test marker string, or
an Alembic revision-hash identifier that is structurally required to look
like a hex string. None of these findings are new in kind to Phase 9.5E --
they are the same false-positive classes every prior phase's own secret
scan already documented and accepted. **No remediation required.**
