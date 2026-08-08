# Phase 1 residual risk register

What remains open after this wave closes, in order of what actually
matters.

1. **No real ISTD connection exists.** By design — `providers/direct_istd.py`
   raises `NotImplementedError`. Nothing about this feature does anything
   to a real customer's tax filings today, regardless of the settings
   toggle. See `../phase2/phase2-seam.md`.

2. **No Android UI.** Deliberate scope cut this session, not an oversight:
   Android's Chaquopy-embedded Python backend already carries the full
   feature (it imports and runs the same `commercial_runtime/einvoicing/`
   code as desktop), but there is no Kotlin settings/credentials/queue
   screen, so an Android install has no way to turn the feature on at all
   today. Zero risk to Android's existing behavior (verified: `segno`'s
   import is function-local, not module-level; `cryptography` was already
   a Chaquopy dependency) — this is a missing capability, not a
   regression. Needs a real Android/Gradle toolchain to build and verify;
   none was available in the environment this wave was built in.

3. **Rollback level 5 (redeploy a previous build) was not drilled with an
   actual second executable this wave** — it rests on
   `einvoicing_migration_test.py::test_v1_shaped_read_still_works_against_v2_file`,
   which proves the underlying claim without a real second binary.
   Levels 1–4 **were** drilled live against a real running install with a
   real queued invoice — see `operational-runbook-and-kill-switch.md`'s
   drill log.

4. **Cosmetic UI gaps:** no status badge on the on-screen receipt modal, no
   status column in the transactions list. Both non-load-bearing — the
   authoritative queue view (`einvoicing.html`) already shows this
   information.

5. **Retail's pre-existing `sales.sale_number` cross-company collision bug**
   (found during `invoice-numbering-audit.md`, unrelated to e-invoicing
   correctness) and **Clinic's non-sequential `invoice_number`** are
   real, separate issues, tracked but not fixed here — the dedicated
   `einvoice_sequence` this feature uses is unaffected by either.

6. **`settings_capability`/`capability_guard` in `routes.py` are wired but
   unused** — Phase 1 gates settings/credential writes on an admin
   session only, not a fine-grained license capability. Low risk (admin
   session is already the correct minimum bar), documented as a known gap
   rather than silently absent.

7. **No real ISTD rate limits or error-code vocabulary exist to test
   against** — `worker.py`'s backoff/rate-limiting is generically
   reasonable (exponential + jitter, capped, bounded attempts) but not
   tuned to any real authority's actual documented limits, because those
   documents don't exist yet either.
