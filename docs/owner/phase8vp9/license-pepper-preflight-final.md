# Phase 8V-P9 — License-Pepper Preflight — Final

## Real incident this closes

Phase 8V-P7's own Scenario 6/7 work hit a real, self-caught bug: a throwaway validation script called
`app.config.get('LICENSE_KEY_PEPPER', 'dev-pepper')` -- the wrong config key name (canonical is
`LICENSE_PEPPER`), silently falling back to a hardcoded literal, producing a license key whose HMAC
verification failed against the real Owner instance. Caught only by inspecting real captured wire
evidence. This session hardens `flask commercial preflight` so a class of pepper misconfiguration is
caught deterministically rather than by luck.

## Real source confirmed first (not assumed)

- Issuance: `owner/app/licensing/routes.py:93` reads `current_app.config["LICENSE_PEPPER"]`.
- Verification: `owner/app/licensing_service/activation.py:108` reads `config["license_pepper"]`, a
  key populated in `owner/app/api_external/routes.py:29` from the exact same
  `current_app.config["LICENSE_PEPPER"]` attribute. **One real source, both directions -- issuance and
  verification cannot structurally drift from each other in the real app.** (My own prior bug was in a
  standalone script that bypassed this wiring entirely, not a flaw in the real app.)

## New preflight checks added (`owner/app/commercial_ops/preflight.py::_check_license_pepper()`)

1. `license_pepper_configured`: FAIL if empty/unset; WARNING (not blocking) if it's the known dev
   placeholder value (`validate_external_api_production()` already separately, unconditionally refuses
   real production startup with this value -- this is a dev-mode reminder, not a new production gate);
   OK otherwise. Never logs the pepper value.
2. `license_pepper_self_test_roundtrip`: a real `hash_license_secret()`/`verify_license_key()`
   round-trip against a synthetic constant key (`AURA-PREFLIGHT-SELFTEST-...`), using the exact
   configured pepper -- genuine proof the pepper is usable, not just present.
3. `license_pepper_no_stray_aliases`: WARNING if any of `LICENSE_PEPPER`, `LICENSE_KEY_PEPPER`,
   `OWNER_LICENSE_KEY_PEPPER`, `OWNER_PEPPER`, `PEPPER` are set as environment variables (none of
   which Owner ever reads -- the canonical name is `OWNER_LICENSE_PEPPER`) -- a direct, real defense
   against exactly the class of mistake found in Phase 8V-P7.

All three wired into `run_preflight()`'s blocking check chain (only the first two affect `ok`; the
alias check is informational, since a stray alias doesn't affect the real, correctly-configured
pepper).

## Tests added (real, passing)

5 new tests in `owner/tests/test_commercial_ops_preflight.py`: default-OK, empty-fails,
dev-placeholder-is-warning-not-failure, round-trip-never-logs-the-real-value (asserts a distinctive
pepper string never appears in any check's own `detail` text), stray-alias-is-warning-not-failure.
13/13 passing (8 original + 5 new).

## Real, live confirmation (not just unit tests)

```
$ flask commercial preflight
ok: true
license_pepper_configured WARNING - ...known development placeholder value...
license_pepper_self_test_roundtrip OK - Real HMAC hash/verify round-trip against a synthetic key succeeded.
license_pepper_no_stray_aliases OK - No known misspelled pepper env-var aliases are set.
```

Real, run against the actual dev Owner environment this session's own physical validation uses.

## Requirements checklist (per governing spec Part F)

- Canonical pepper configuration key exists: checked. Value nonempty: checked. Issuance/verification
  same source: confirmed structurally from source (single attribute, not re-derived). Misspelled/
  deprecated aliases detected: checked (5 known aliases). Safe deterministic self-test: real HMAC
  round-trip. No pepper value printed: confirmed by test. No raw license key printed: the self-test
  uses only a synthetic constant, never a real key. Failure returns nonzero: `run_preflight()`'s
  `ok=False` propagates through the existing CLI command's exit-code wiring (unchanged this session).
  Error is actionable: every FAIL/WARNING detail names the exact fix.
