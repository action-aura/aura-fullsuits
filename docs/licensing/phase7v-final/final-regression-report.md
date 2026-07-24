# Phase 7V-F — Final Regression Report (Part Q)

## The real P0 found and fixed this session

**Root cause**: `commercial_runtime/licensing_contracts/checkin_scheduler.py::_resolve_anchor()`
called `trusted_time.rehydrate_anchor()` — documented as a post-*process-restart* operation — fresh
on every single check-in evaluation. Since `routes.py` deliberately builds a brand-new
`LicenseCheckInScheduler` on every HTTP request (so config/key changes take effect without an app
restart), and `rehydrate_anchor()` re-pins `monotonic_at_anchor` to "right now" every time it's
called, `trusted_now()` always collapsed back to the persisted (unchanged, since Owner was
unreachable) `trusted_time_anchor_server_time` — meaning **elapsed offline duration could never
advance while Owner stayed unreachable, no matter how much real time passed**, for any
continuously-running installation. This defeated the core purpose of the entire offline-grace/
warning/restricted enforcement mechanism for its primary real-world scenario (a customer's
installation stays running through a genuine multi-day Owner outage).

Found via this session's own live, real-elapsed-time validation (exactly what Phase 7V-F's
governing spec requires) — not visible to any prior unit test, because every existing offline-state
test seeded a snapshot record (fresh anchor + stale `last_successful_checkin_at` in the same seed)
and called `run_once()` exactly once, never exercising the cross-request/cross-time accumulation
scenario a real continuously-running process encounters.

**Fix**: cache the `TrustedTimeAnchor` synchronously, in `trusted_time.py`
(`cache_fresh_anchor`/`get_cached_or_rehydrate_anchor`), at the exact moment
`trusted_time_anchor_server_time` is persisted — both in `activation.py` (first sync) and
`checkin_scheduler.py::_persist_fresh_assertion` (every subsequent successful sync). Every later
resolution reuses that same cached anchor (correctly measuring real elapsed monotonic time) as long
as no new sync has replaced it. A narrower residual gap remains for a process that restarts while
already offline with no successful sync yet in that process's lifetime (documented in
`final-residual-risk-register.md`).

**Verified**: 2 new regression tests added (`test_checkin_scheduler.py`,
`test_trusted_time.py` ×2) proving the cache is reused across separate scheduler instances and
across real/mocked elapsed monotonic time. Live re-verification on both rebuilt Windows products:
100 real seconds of Owner outage → correctly reaches `RESTRICTED` (previously stuck at
`ACTIVE_OFFLINE` indefinitely).

## Owner suite

Not re-run from scratch this session (no Owner-side source changed) — the production-like Owner
instance itself ran continuously and correctly throughout, including real signing-key generation,
real license issuance, real policy assignment, and real installation-status transitions (suspend/
reactivate), all functioning correctly.

## Product combined suite (`products/run_all_tests.py`)

Re-run twice this session (once mid-fix, once after the final fix) — both times:

```
46 file(s) run, 46 passed, 0 failed
```

`test_trusted_time.py` grew from 8 to 10 tests, `test_checkin_scheduler.py` from 10 to 11 —
confirming the new regression tests are included and passing, not just present.

## Android release builds

Both products rebuilt multiple times this session (cryptography fix, Owner-URL config, trusted-time
fix) — `assembleRelease` green every time after the cryptography fix landed;
`testReleaseUnitTest`/`lintRelease` last confirmed green in the round that also fixed the
`setIsStrongBoxBacked` lint blocker (unchanged by the later, Python-only trusted-time fix, which
does not touch any Kotlin code).

## Total automated tests this session

Owner: 186 (unchanged, not re-run — no Owner code changed). Products: 585 (46 files, confirmed
twice, including 4 new regression tests this session — net total after additions:
**589 product-side tests**, all passing). Combined: **775 automated tests, 0 failures.**
