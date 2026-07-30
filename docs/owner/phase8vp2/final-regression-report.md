# Phase 8V-P2 — Final Regression Report

All totals below are from this session's own runs, not reused from any prior phase.

## Owner: **394/394 passing, 0 failures**

```
$ python -m pytest -q   (owner/, real Postgres test database)
394 passed in 529.62s
```

380 baseline (Phase 8V-P) + 6 new Scenario 7 tests + 8 new preflight tests = 394. (One intermediate
run showed 386 before the preflight suite was added; final run with all new tests present is 394.)

## commercial_runtime: **219/219 passing, 0 failures**

```
$ python -m pytest -q   (commercial_runtime/)
219 passed in 11.02s
```

## Product backends

Not re-run this session -- no product backend code (Clinic/Retail Flask apps, licensing client,
capability guards, financial calculations, migrations, backup/restore/export) changed. The Phase
8V-P session's own full Windows-lifecycle validation (`final-regression-report.md` in that phase's
doc set) already covers this at a more recent point than the last product-backend source change, and
nothing in this phase touched `products/clinic/backend/` or `products/retail/backend/`.

## Clinic Android: unit + lint **PASS**

```
$ ./gradlew --no-daemon testDebugUnitTest   -> BUILD SUCCESSFUL in 2m 39s
$ ./gradlew --no-daemon lintRelease         -> BUILD SUCCESSFUL in 4m 12s
$ ./gradlew --no-daemon assembleRelease bundleRelease -> BUILD SUCCESSFUL in 2m 14s
```

## Retail Android: unit + lint **PASS**

```
$ ./gradlew --no-daemon testDebugUnitTest lintRelease -> BUILD SUCCESSFUL in 2m 53s
$ ./gradlew --no-daemon assembleRelease bundleRelease -> BUILD SUCCESSFUL in 1m 39s
```

## Migration reconfirmation

```
$ alembic current   -> 0f8d55b753ed (head)
$ alembic heads     -> 0f8d55b753ed (head)
```

Dev database is exactly at head. No schema migration was needed or added this session (the Scenario
7 fix and the preflight command are pure application-logic additions against existing tables/columns).

## Security

Secret scan: none of this session's diffs (`renewal_requests.py`, `preflight.py`, `cli.py`, three
test files) contain any credential, key, or password literal -- confirmed by direct read of every
changed file above. Release-artifact scan: see `final-android-artifact-evidence.md` (no debug
config, no TLS bypass, no fake state). No Logcat review performed (no device -- see
`physical-android-readiness.md`). No new Android traffic captured this session (no device).

## Totals summary

| Suite | Result |
|---|---|
| Owner | 394/394 |
| commercial_runtime | 219/219 |
| Clinic Android unit+lint+release build | PASS |
| Retail Android unit+lint+release build | PASS |
| Product backends | Unchanged, not re-run |
| Migrations | At head, no drift |
| P0 remaining | 0 |
| P1 remaining | 0 |
