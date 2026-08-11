# Phase 9R — M2: Environment Separation (Executed)

## What shipped

- `owner/app/config.py`: 6 new fail-closed startup checks added to
  `BaseConfig.validate()` (trusted-proxy count, host allowlist, backup
  target, scheduler-role ambiguity, unknown-config-key typo detection,
  default/weak-secret and malformed/wrong-environment database URL
  detection strengthened beyond the pre-existing pepper-only check). Full
  rule table in `configuration-contract.md`.
- Fixed a real pre-existing bug found while extending this code: the
  required-secrets presence check read `os.environ` directly instead of
  the resolved class attribute, inconsistent with every other check in the
  same method and untestable via the class-attribute-override pattern used
  throughout this codebase's own config tests.
- `owner/.env.example` extended with every new variable, placeholder-only.
- `owner/tests/test_phase9r_environment_separation.py` — 27 tests, one per
  failure mode plus explicit "does not wrongly reject a legitimate config"
  cases (co-located `localhost` production database, explicit `worker`
  scheduler role, known config keys never flagged in strict mode).

## Test evidence

```
$ python -m pytest tests/test_phase9r_environment_separation.py -v
27 passed in 5.97s

$ python -m pytest tests/test_data_boundary.py tests/test_security_headers.py tests/test_commercial_ops_preflight.py -q
23 passed in 58.00s
```

A full Owner-suite rerun was not repeated for this milestone: the new
`validate()` logic only executes when `ENV` is `staging`/`production` *and*
`TESTING` is `False` — every existing test in the suite runs under
`TestingConfig` (`TESTING = True`), which hits the function's first line
(`if cls.ENV == "development" or cls.TESTING: return`) before any new code
runs. The blast radius of this change is provably limited to: (a) this
milestone's own 27 tests, and (b) the three existing files that construct
`ProductionConfig`/`StagingConfig` directly or exercise
`validate_external_api_production()` — all three re-run above, clean. This
reasoning will be re-verified by the M0-style full regression at M28 (final
regression) regardless.

## Environment matrix (see `configuration-contract.md` for the full rule table)

| Environment | Secret validation | Cookie `Secure` |
|---|---|---|
| Development | Exempt | `False` |
| Test (`TESTING=True`) | Exempt | n/a |
| Staging | Full | `True` |
| Production | Full | `True` |

## What's still open for later milestones

- Staging/production secret-collision detection (the same secret reused
  across environments) is procedural, not an in-process check — enforced by
  the deployment pipeline (M16), documented in `configuration-contract.md`.
- `OWNER_TRUSTED_PROXY_COUNT` and `OWNER_ALLOWED_HOSTS` are validated for
  *presence* here; actually wiring them into Werkzeug's `ProxyFix` and
  Flask's host-matching is M6/M7 (edge security), once Caddy exists to sit
  in front of the app.
- `OWNER_SCHEDULER_ROLE` is validated for *validity* here; the scheduler
  process itself reading and obeying this value is M5 (application-server
  and scheduler topology).
- `OWNER_BACKUP_TARGET_URL` is validated for *format* here; the backup
  tooling that actually uses it is M12.
