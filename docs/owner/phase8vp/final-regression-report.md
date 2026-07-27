# Phase 8V-P — Final Regression Report (Part U)

Exact totals, this session, at final HEAD (not reused from any prior phase's numbers).

| Suite | Result |
|---|---|
| `owner/tests/` | **380 passed**, 0 failed, 0 unexplained skips (379 Phase 8V baseline + 1 new: `test_same_device_activating_a_different_license_rejected_cleanly`) |
| `commercial_runtime/licensing_contracts/tests/` | **214 passed**, 0 failed (reconfirmed clean after the `DEVICE_ALREADY_REGISTERED` fix — that fix lives entirely in `owner/`, this suite is an unaffected-but-reconfirmed control) |
| Product backends (Retail/Clinic combined runner) | **NOT RE-RUN** — zero Python product-backend code changed this session (only two frontend `.js` files, both from Phase 8V, and zero product-backend files this session); re-running unrelated suites would not add evidence, and the baseline doc's own pre-existing cross-file isolation issue (documented in `docs/owner/phase8/phase8-scope-and-baseline.md`) means "run the whole combined suite" has never been a meaningful single command |
| Android Clinic | **NOT RE-RUN** (last real result: 82/82, Phase 8V/Milestone 7, unchanged Kotlin since) |
| Android Retail | **NOT RE-RUN** (last real result: build successful, Phase 8V/Milestone 7, unchanged Kotlin since) |

## Real product-level validation this session (not a pytest suite, but real evidence)

Five real end-to-end commercial scenarios against two real installed Windows products and a real
Owner server — see `scenario-1` through `scenario-7-*-evidence.md`. One real P0-class defect found
and fixed (`DEVICE_ALREADY_REGISTERED`), regression-tested above. One real environment-state gap
found and fixed (stale trust anchor / signing key). One real environment-state gap found and fixed
(stale permission seed data). One real, disclosed feature gap found and **not** fixed (renewal
device-allowance -> license device-limit sync — see `final-residual-risk-register.md`).

## Total: 594 automated tests passed (380 + 214), 0 failed, across this session's regression scope.
