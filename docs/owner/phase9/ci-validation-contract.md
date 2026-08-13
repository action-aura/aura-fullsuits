# Phase 9 Milestone 11 — CI Validation Contract

## Status: E-W0.3 real CI, first fully green run on actual GitHub Actions infrastructure

Everything below this line was manual-run-only until this follow-up session. PR #2
(`https://github.com/action-aura/aura-fullsuits/pull/2`, `chore/owner-ci-hardening-w0.3`) opened
specifically to get a real run, since `push` only triggers CI for `master`/`phase9/**`/`phase9.5/**`/
`feat/**` branches (not `chore/**` — this branch had never run CI at all despite E-W0.3's segno-pin fix
already being merged). First run hit real, previously-undetected failures one job category at a time;
run `31727459119` (commit `a61e8a139d82c8039c2dac411609a07e603ca97a`) is the first fully green one —
**9/9 checks passing**. Every fix below is a real bug this exact process caught, not a re-run of a flake.

Real findings and fixes, in the order CI surfaced them:

1. **DPAPI tests crashed on the Linux runner** (`AttributeError: module 'ctypes' has no attribute
   'windll'`) — `commercial_runtime/einvoicing/tests/test_credentials.py` and `test_routes.py` assumed
   "this environment is genuinely Windows" and called real DPAPI unconditionally. DPAPI has no Linux
   equivalent (`AppSecretDerivedSecretBox` is the intended cross-platform fallback). Fixed by extending
   the `skipif(sys.platform != 'win32', ...)` pattern `licensing_contracts`' own tests already used —
   this suite alone had missed it.
2. **Bandit MEDIUM (B704 markupsafe_markup_xss)**, real finding: `owner/app/i18n.py`'s `bidi_isolate`
   filter did `Markup(f'...{escape(value)}...')` — safe in practice (value is pre-escaped) but statically
   unverifiable. Rewrote as `Markup('...{}...').format(value)`, markupsafe's own auto-escaping form —
   provably safe, no manual `escape()` needed, byte-identical output verified against all 3 existing
   pinned tests in `owner/tests/test_phase9_5b_r_bidi_safety.py`.
3. **staging-package job**: `docker-compose.staging.yml` needs `.env.staging`, which doesn't exist in a
   fresh checkout — the compose file's own header comment documents copying
   `deploy/staging/.env.staging.example` first, but the workflow never did it. Added that step (the
   NAMES-ONLY template, no real secret involved).
4. **`pg_dump` version mismatch**: `ubuntu-latest` ships `pg_dump 16.14` by default; the service
   container is `postgres:17.10`. `pg_dump` refuses to dump a server newer than itself —
   `test_backup_restore.py`'s real `pg_dump` calls hit this directly. Needed the PGDG apt repo added
   first (`apt.postgresql.org.sh`, ships in `postgresql-common`) before `apt-get install
   postgresql-client-17` — the naive `apt-get install` alone failed with "Unable to locate package"
   since the v16 package is baked into the runner image directly, not via an active PGDG source.
5. **Path traversal in attachment filename sanitization** — real bug in
   `owner/app/expenses/attachments.py::_sanitize_display_filename`: used `os.path.basename()`, which is
   platform-dependent (only splits on `/` on POSIX, not `\`), so a Windows-style traversal payload
   (`..\..\..\secrets.pdf`) sailed through untouched on the Linux runner — caught for real by
   `test_attachment_filename_traversal_is_sanitized_never_used_as_path`. Not exploitable for actual file
   writes (`storage_key` is always server-generated, never derived from the name), but
   `original_filename` is stored and used for the download `Content-Disposition` header, so this closed
   a real gap against the module's own stated threat model. Fixed by normalizing both separator styles
   explicitly instead of relying on the host platform's `basename()`.
6. **CI Postgres role was a real superuser** — `test_application_role_is_not_superuser`
   (`test_phase9r_postgresql_hardening.py`) failed for real: the official `postgres` Docker image always
   makes its bootstrap `POSTGRES_USER` a superuser (that's just how `initdb` works), unlike real
   dev/staging Postgres where a human explicitly runs `CREATE ROLE aura_owner ... CREATEDB;` (no
   `SUPERUSER`) — see "No superuser application account" below. First fix attempt (`ALTER ROLE aura_owner
   NOSUPERUSER`) failed with a real, hard Postgres guarantee: *"the bootstrap superuser must have the
   SUPERUSER attribute"* — the cluster's bootstrap role can never lose it, full stop. Real fix: leave
   `POSTGRES_USER` unset (bootstraps as `postgres` instead), then explicitly `CREATE ROLE aura_owner
   LOGIN PASSWORD 'aura_owner_dev' CREATEDB NOSUPERUSER NOCREATEROLE;` and `CREATE DATABASE
   aura_owner_test OWNER aura_owner;` — the same real dev/staging pattern, just applied once per
   ephemeral CI container instead of once by a human.
7. **Subscription-duplication race under concurrent fulfillment** — real financial-correctness bug,
   caught for the first time by genuine concurrent Postgres load (never previously exercised on real
   infra): `test_item3_concurrent_fulfillment_requests_only_one_creates_subscription` got 4 Subscriptions
   from 5 concurrent `fulfill_order()` calls for the same paid `SalesOrder`, not 1. Root cause:
   `fulfill_order()`'s `SELECT ... FOR UPDATE` row lock is released the instant `create_subscription()`
   commits internally (a deliberate, already-documented tradeoff — the function can't wrap the whole
   multi-step sequence in one outer transaction without changing `create_subscription()`/
   `create_license()`'s own commit-per-step behavior, needed for crash recovery). `order.status` only
   flips to `FULFILLED` in the function's own final commit, several steps later, so a second concurrent
   caller can acquire the now-released lock, still see the order as not-yet-fulfilled, and also create a
   Subscription. Fixed with a real DB-level backstop (reviewed and approved before push, payment-adjacent
   code): migration `5de3f36f4c21` adds `UNIQUE (sales_order_id)` on `owner_subscriptions` (nullable-safe
   — Postgres allows unlimited NULLs, so subscriptions created outside fulfillment are unaffected), paired
   with the matching `__table_args__` on the `Subscription` model (a first attempt without it made
   `alembic check` correctly catch real model/migration drift), and `fulfillment.py` catching the
   resulting `IntegrityError` on the losing side to reuse the winner's row — the same reuse pattern the
   function already used for crash-recovery retries, not a new code path.
8. **Secret scanning had no way to pass** — the original step did `detect-secrets scan --all-files` with
   a zero-tolerance assert and no baseline. This codebase legitimately contains ~163 detect-secrets
   matches (dev-only placeholder secrets, `localhost`-only dev DB connection strings, synthetic test
   fixtures, Alembic's own auto-generated revision hashes, historical evidence docs) — all already
   individually reviewed and justified in
   `docs/owner/phase9_5b_r3/secret-scan-false-positive-review.md`. That review explicitly chose not to
   add a baseline file at the time, but the as-written CI step had no way to distinguish already-reviewed
   content from a genuinely new secret — it was really asserting "zero dev fixtures exist," not "zero
   secrets exist," and could never pass. Added `.secrets.baseline`: every finding marked
   `is_secret: false` using that same review's own categorization — not a blanket allowlist,
   `detect-secrets scan --baseline` only reports entries NOT already in it, so a genuinely new secret
   still fails the gate. Generating the baseline correctly required a real detour: a first attempt
   generated it on this Windows dev machine, which proved unreliable (directly verified: the same file
   scanned 0 hits on Windows vs. 3 real hits on Linux CI) — a baseline has to be generated on the same
   platform CI runs on. Solved by temporarily uploading the post-scan `.secrets.baseline` as a CI
   artifact (`if: always()`, `include-hidden-files: true` — dotfiles are skipped by `upload-artifact@v4`
   by default, the exact same gotcha the pre-existing coverage-shard upload step already had to work
   around) to pull the real Linux-computed hashes, then removed once its job was done.

## Command-by-command evidence (real CI run 31727459119, all 9 jobs green)

| Stage | Job | Real result |
|---|---|---|
| Owner tests | `owner-tests` (4-way matrix) | All 4 shards pass — A, B, D each ~4min, C (heaviest: phase9r/phase8/phase9_5b) ~5min, each its own fresh `postgres:17` container |
| Owner coverage baseline | `owner-coverage` | Passes — combines all 4 shards' `.coverage` data, publishes via `$GITHUB_STEP_SUMMARY` |
| Owner migrations | `owner-migrations` (`alembic upgrade head` + `alembic check`) | Passes — `alembic check` now also validates the Subscription model against the real applied schema, catches drift like item #7 above for real |
| commercial_runtime / Retail / Clinic tests | `product-tests` | Passes |
| Security and supply-chain scans | `security-scan` (bandit, detect-secrets, pip-audit, SBOM) | Passes — bandit 0 HIGH/MEDIUM, detect-secrets 0 unaudited, pip-audit clean |
| Staging deployment package | `staging-package` (`docker compose config`) | Passes — real dry validation against the NAMES-ONLY `.env.staging.example` template |

`+12 more owner-tests` once the separate, still-open payment-SoD/cash-closing-IDOR PR
(`feat/owner-week2-sod-idor-fix`) merges — not yet re-verified against that branch, tracked separately.

## Requirements satisfied by the workflow definition (not all independently re-verified as CI behavior)

- No secrets available to untrusted PRs: the workflow uses only test-time synthetic values
  (`OWNER_TEST_DATABASE_URL` matching `owner/tests/conftest.py`'s own default) — no `secrets.*` context
  reference exists anywhere in the file, so there is nothing for a fork PR to exfiltrate.
- No signing key in the repository — confirmed structurally (`.gitignore`/`.dockerignore` both exclude
  `var/signing-keys`; Milestone 5/10 secret scans found nothing).
- No automatic production deployment — the workflow's final stage renders the staging Compose config
  (`docker compose config`, a dry validation, not `up`); nothing in this repository triggers a real
  deploy automatically, matching Non-Negotiable Principle 9.
- Deterministic commands, machine-readable results — every stage's command is the literal command this
  session ran manually and recorded exact output for.

## Android/Windows signing

Not built into this CI workflow — see `release-workstation-runbook.md`. CI runs Owner/backend tests
only; product artifact signing happens on a controlled release workstation, never in an untrusted CI
runner, per the governing instruction's own explicit requirement.
