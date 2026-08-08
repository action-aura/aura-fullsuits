# Phase 1 implementation plan — as executed

Original plan produced by an Opus planning pass before implementation
started, grounded in a full read of the actual codebase (invoice numbering,
migration tooling, backup scope, receipt printing, Android architecture,
Chaquopy constraints, and every reusable pattern in `commercial_runtime/`).
Executed in the order below, each step tested before moving to the next.

## Order followed

0. Baseline: `python products/run_all_tests.py` — 46 files, 564 tests,
   clean, before any Phase 1 code existed.
1. `invoice-numbering-audit.md` — written first because it justifies the
   single most consequential design decision (a dedicated `einvoice_sequence`,
   not a reuse of `sale_number`/`invoice_number`).
2. Schema + migration (`schema.py`, both products' `SCHEMA_VERSION` bump) —
   lands before any consumer; an install that upgrades and stops here is
   completely inert.
3. Regression suite, written and green *before* any feature code —
   `retail_einvoicing_regression_test.py` / `clinic_einvoicing_regression_test.py`
   freeze the disabled-path response shapes and table schemas as the guard
   rail everything after this must not disturb.
4. Settings + kill switch.
5. Credentials (encrypted at rest).
6. Sequence allocator.
7. Document model + UBL + QR (`segno` added to `requirements/base.txt`).
8. Provider layer (`base.py`, `mock.py`, `direct_istd.py` stub).
9. Outbox + state machine.
10. Audit trail.
11. Outbox worker.
12. HTTP routes blueprint.
13. Retail wiring — `core/retail/einvoice_adapter.py`, `app.py`,
    `retail_api.py::create_sale()`. Regression suite re-run and confirmed
    unchanged; `retail_einvoicing_test.py` integration suite added.
14. Clinic wiring — same shape, `core/clinic/einvoice_adapter.py`. All
    bugs found and fixed during Retail's wiring (see below) were avoided
    outright here.
15. Desktop UI — `einvoicing.html`/`.js` for both products, receipt QR
    block in `subsystem-retail.js`/`subsystem-clinic.js`.
16. Android UI — **explicitly skipped this wave**, see
    `phase1-residual-risk-register.md` item 2.
17. Packaging — both `.spec` files updated;
    **verified with a real PyInstaller build** of Retail (not just
    inspection): frozen exe launched, `/api/health` and
    `/api/einvoicing/status` both responded correctly, confirming the
    hidden-import fix actually works.
18. Test-runner registration (`run_all_tests.py::SUITES['einvoicing']`) +
    coverage gate: **97% on `commercial_runtime/einvoicing/`** (169 tests),
    well above the 80% target.
19. This documentation set.
20. Kill-switch rollback drill — see `operational-runbook-and-kill-switch.md`
    for what was actually exercised.

## Real bugs found and fixed along the way (not hypothetical)

1. **`outbox.py::_transition`** conflated "stale" (a race, should return
   `False`) with "illegal" (a genuine caller bug, should raise) — fixed to
   distinguish terminal-state staleness from a non-terminal illegal call.
2. **`worker.py::_process_row`** didn't catch exceptions from
   `document_builder()` — a single row with unbuildable data (e.g. no
   buyer ID) would crash the entire worker pass, not just that row. Fixed,
   with a dedicated crash-simulation test proving later rows in the same
   batch still get processed.
3. **`qr.py`** — segno's SVG writer emits `bytes`, not `str`; using
   `io.StringIO()` raised a `TypeError`. Fixed to `io.BytesIO()`.
4. **`routes.py`** QR endpoints ignored the requested `.svg`/`.png`
   extension entirely, always deferring to whatever `qr_for_outbox_row`
   returned. Fixed to honor the extension when locally rendering (a
   provider-supplied image is served as-is regardless, since it can't be
   losslessly reformatted).
5. **Retail's `core/retail/einvoice_adapter.py::build_document`** had a
   parameter signature mismatch against what `worker.py` actually calls
   (`document_builder(conn, row)` vs. a 4-argument signature) — caught
   immediately by the integration test, not by production traffic.
6. **Reconciliation timestamp format mismatch** (`enabled_at` UTC ISO-8601
   vs. each product's own `created_at` convention) — see
   `outbox-state-machine.md`. The single most subtle bug in this wave;
   would have silently made the "recover a lost enqueue" safety net a
   permanent no-op without ever raising an error.

Every one of these was caught by a test before merge, in this exact
session — not discovered later in production.
