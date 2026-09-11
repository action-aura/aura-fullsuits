# Retail test suites — index

200 suites live in this one flat directory. This file exists so you can find
the right one without grepping 200 filenames.

Every description below is the suite's OWN first docstring line, read out of the
file — not a hand-written summary that goes stale the week after it is written.

## How to run them

```bash
# everything (what CI runs — Python and Node, one subprocess per file)
python products/run_all_tests.py retail

# one Python suite
<venv>/Scripts/python.exe -m pytest products/retail/tests/<name>_test.py -q

# one Node suite (no framework, no build step — plain node)
node products/retail/tests/<name>_test.js
```

**One test FILE per pytest process.** These suites boot a Flask app at import,
so several in one process poison each other (AUDIT-010). The canonical runner
does this correctly; hand-rolled `pytest` invocations over several files do not.

## Why this directory is flat, and must stay flat

`discover()` in `products/run_all_tests.py` finds suites with a **non-recursive**
`d.glob('*_test.py')` over directories listed explicitly in `SUITES`. Move these
files into subfolders and they stop being discovered — the run still reports
green, having silently run nothing. That is why the organisation lives in this
index rather than in the filesystem.

A suite is picked up only if its filename ends in `_test.py` or `_test.js` (or
begins with `test_`). A file named anything else is dead weight nothing runs.

## Reading the suites

Two things worth knowing before you trust a green run:

- The suites that actually RENDER screens and measure real computed styles are
  `retail_design_render_test.js` (contrast over a corpus of every screen) and
  `retail_design_focus_test.js` (focus rings, 44px touch floor). Most other
  "UI" suites assert against source text, which cannot catch a screen that
  renders wrong.
- `retail_route_reachability_test.py` asks whether ANY shipped client calls each
  route. It cannot ask whether the RIGHT screen calls it — a route reachable
  only from a settings test button passes it.

---

## Money path — sales, returns, payments, cash drawer  (29 suites — 21 py, 8 js)
  - `retail_cash_drawer_test.py` — shift / cash-drawer management (feat/shift-cash-drawer,
  - `retail_changed_field_delta_test.py` — multi-device sync: changed-field deltas (launch-readiness
  - `retail_checkout_credit_walkin_guard_test.js` — Regression test for a missing client-side guard on Credit-method sales.
  - `retail_checkout_error_toast_test.js` — Regression test for a silent-failure bug on the Retail POS Charge button.
  - `retail_dashboard_payment_colors_test.js` — Regression test for the Retail dashboard "Payment Methods" donut chart running out of colors on a day that uses every payment…
  - `retail_design_money_test.js` — OPERATIONAL CALM — money must be unambiguous.
  - `retail_drawer_money_sweep_test.py` — no route may hand anybody ANOTHER TILL's drawer money,
  - `retail_held_sales_test.py` — hold / resume sale ("park a sale") regression suite.
  - `retail_loyalty_redemption_test.py` — loyalty redemption, wave 1 (schema v27, ROADMAP.md's 2026-08-31 "retail schema v27 CLAIMED for loyalty redemption" entry).
  - `retail_loyalty_redemption_ui_test.js` — retail_loyalty_redemption_ui_test.js — the POS "Current Sale" loyalty-point redemption control (launch-readiness wave 1, schema…
  - `retail_loyalty_return_link_test.py` — the loyalty return link (schema v29, ROADMAP.md's CLAIMED: retail v29" entry).
  - `retail_money_leak_runtime_sweep_test.py` — no route may hand a cashier transacted money, proved by CALLING every route as a cashier and reading what comes back.
  - `retail_money_sentence_precision_test.py` — money figures rendered into SENTENCES keep the shop's real
  - `retail_money_sync_test.py` — Phase 5 (launch-readiness): money-moving sync, end to end
  - `retail_notification_money_format_test.py` — Shared money-formatting for notifications: unit + parity coverage.
  - `retail_offline_sales_stop_test.py` — launch-readiness Phase 7 stage 7c-ii: the 72-hour hard stop
  - `retail_payment_chart_negative_bucket_test.js` — Regression test: a NET-NEGATIVE payment-method bucket must never be drawn as a doughnut slice.
  - `retail_payment_grid_test.js` — retail_payment_grid_test.js — the tender grid rebuild (owner brief, 2026-09-08: "the single control a cashier touches on every…
  - `retail_payment_money_precision_test.py` — customer/supplier/PO payment routes keep the shop's real
  - `retail_pricing_parity_test.py` — The till's PREVIEW and the server's CHARGE must resolve the same discount.
  - `retail_pricing_test.py` — tax-calculation-policy and revenue-dashboard regression suite.
  - `retail_promotions_test.py` — promotions, wave 1 (schema v23, ROADMAP.md's 2026-08-30 retail schema v23 CLAIMED for promotions, wave 1" entry). Backend/API…
  - `retail_promotions_ui_test.js` — retail_promotions_ui_test.js — Promotions wave 1, frontend half (ROADMAP.md "retail schema v23 CLAIMED").
  - `retail_report_clock_money_disclosure_test.py` — GET /sales/recent must not hand a cashier the book the report routes refuse them, and the sweep that is supposed to notice must…
  - `retail_return_loyalty_reversal_test.py` — loyalty reversal on returns (schema v27, ROADMAP.md's 2026-08-31 "RETURNS AGAINST A SALE THAT USED POINTS -- decided, not yet…
  - `retail_returns_wave0_test.py` — server-authoritative return/refund regression suite (Wave 0, AUDIT-004).
  - `retail_sale_money_precision_test.py` — create_sale/create_return/hold_sale/cash-movement/statement
  - `retail_stock_sync_cash_drawer_test.py` — Phase 5 wave B (stock-moving sync): a Phase 4 cash drawer
  - `retail_v16_terminal_cash_drawer_test.py` — schema v16 regression coverage: the cash drawer stops belonging to a BRANCH and starts belonging to a TERMINAL (launch-readiness…

## Stock — products, inventory, transfers, suppliers  (30 suites — 23 py, 7 js)
  - `retail_category_delete_fk_sync_test.py` — regression coverage for the schema v3 change
  - `retail_category_delete_route_test.py` — final-review Fix 4 (2026-08-07): `delete_category` error
  - `retail_modifiers_test.py` — restaurant modifiers, wave 1 (schema v26, ROADMAP.md's 2026-08-31 "retail schema v26 CLAIMED for restaurant modifiers, wave 1…
  - `retail_multiline_stock_test.py` — two lines for the SAME product in one sale must not jointly exceed stock (launch-readiness Phase 7 stage 7d-iii follow-up…
  - `retail_offline_banner_stale_stock_test.js` — Phase 7 stage 7b — "tell the truth about being offline (docs/launch-readiness/phase7-offline-ux.md).
  - `retail_oversell_exception_test.py` — schema v20 / stock_exceptions regression coverage: launch- readiness Phase 7 stage 7d-i, the oversell exception queue. See…
  - `retail_oversell_relaxation_test.py` — launch-readiness Phase 7 stage 7d-iii: trust the cashier's eyes over a stale cache, and record it. See docs/launch-readiness…
  - `retail_po_supplier_tenancy_test.py` — cross-tenant leak fix for purchase_orders.supplier_id.
  - `retail_product_barcode_uniqueness_test.py` — product barcode uniqueness (AUDIT, 2026-08-14).
  - `retail_product_lookup_nocase_test.py` — schema v22 / "the case-fold lookup" BEHAVIOUR coverage (ROADMAP.md's 2026-08-30 "retail schema v22 CLAIMED for the case-fold…
  - `retail_product_lookup_test.py` — schema v21 / "the POS scale fix" AND schema v22 / "the case-fold lookup" regression coverage (ROADMAP.md's 2026-08-29 "retail…
  - `retail_product_supplier_tenancy_test.py` — a product may not be filed against another company's supplier.
  - `retail_product_sync_test.py` — multi-device sync foundation: Products wiring.
  - `retail_product_variants_test.py` — product variants, wave 1 (schema v25).
  - `retail_products_table_name_xss_test.js` — Regression test for a stored-XSS bug on the Retail Products & Inventory screen (products/retail/frontend/subsystem-retail.js).
  - `retail_products_uuid_migration_test.py` — Builds a schema-v3-shaped database by hand (INTEGER products.id,
  - `retail_reorder_hook_regression_test.py` — reorder automation foundation
  - `retail_reorder_migration_test.py` — schema v8 migration regression coverage (reorder automation foundation, feat/reorder-automation-foundation -- see…
  - `retail_reorder_sync_test.py` — reorder automation foundation
  - `retail_scanner_input_mode_test.js` — Regression test for: Scanner Settings "Input Mode" (Auto Detect vs Keyboard HID) was saved to localStorage but never read by the…
  - `retail_stock_accuracy_screen_route_test.py` — the Stock accuracy screen's DATA CONTRACT (Phase 3, docs/launch-readiness/phase3-ledger-truth.md).
  - `retail_stock_accuracy_screen_test.js` — Aura Retail — the STOCK ACCURACY screen (Phase 3, docs/launch-readiness phase3-ledger-truth.md).
  - `retail_stock_accuracy_test.py` — stock-accuracy sweep regression suite.
  - `retail_stock_sync_apply_hardening_test.py` — Phase 5 wave B (stock-moving sync): apply-side hardening
  - `retail_stock_sync_test.py` — Phase 5 wave B (launch-readiness): stock-moving sync, end
  - `retail_stock_transfer_test.py` — inter-branch stock transfers (launch-readiness, schema v28).
  - `retail_stock_transfer_ui_test.js` — the Stock Transfers screen actually wires up to the six routes retail_api.py already ships.
  - `retail_supplier_modal_xss_test.js` — Regression test for a stored-XSS bug on the Retail Suppliers screen (products/retail/frontend/subsystem-retail.js).
  - `retail_supplier_name_apostrophe_onclick_test.js` — Regression test for a broken-UI bug on the Retail Suppliers screen (products/retail/frontend/subsystem-retail.js).
  - `retail_supplier_sync_test.py` — multi-device sync foundation: Suppliers wiring.

## Licensing & entitlements  (4 suites — 4 py, 0 js)
  - `retail_licensing_message_scoping_test.py` — licensing failure copy: clock advice must be scoped, and the three copies of the reason-code buckets must stay in step.
  - `retail_licensing_nav_discoverability_test.py` — device licensing page discoverability regression.
  - `retail_registry_v4_activation_split_state_test.py` — Registry v4 -- Defect 2 (launch-readiness Phase 5 verification, HIGH):
  - `retail_release_version_consistency_test.py` — One release version, declared in eight files, checked by nothing until now.

## Identity, capabilities & access control  (25 suites — 20 py, 5 js)
  - `retail_account_quarantine_route_test.py` — the registry `sync_apply_quarantine` READ route (`GET /api/sub/retail/account-quarantine`).
  - `retail_accounting_export_test.py` — launch-readiness "the till scans mixed-case codes" + no journal export, no CSV dump" fixes.
  - `retail_attribution_i18n_test.py` — the two contracts the attribution UI rests on.
  - `retail_attribution_stamp_structural_test.py` — STRUCTURAL guard on the schema-v13 attribution stamp.
  - `retail_attribution_stamp_test.py` — end-to-end proof that every write stamps the schema-v13 attribution columns (launch-readiness Phase 2).
  - `retail_attribution_ui_test.js` — Retail — surfacing v13 attribution (who rang it, on which till) in the UI.
  - `retail_auth_hardening_test.py` — authentication hardening (multi-device Phase 1, Part A).
  - `retail_capability_guard_test.py` — Phase 7 Part T capability-guard verification.
  - `retail_capability_ratchet_consumption_test.py` — the guard on the guard: a capability ratchet must require the verdict to be CONSUMED, not merely computed.
  - `retail_employee_code_after_sync_test.py` — inviting staff must still work on a device whose staff list arrived by sync.
  - `retail_employee_created_via_screen_can_work_test.py` — a cashier created from the real Employees screen must be able to use the retail app it was created for.
  - `retail_employee_management_test.py` — the owner's employee-management screen (multi-device Phase 1).
  - `retail_employee_setup_link_test.js` — retail_employee_setup_link_test.js — the employee-invite "#setup/<token> landing screen (app-shell.js's…
  - `retail_employees_screen_test.js` — Runtime tests for the Retail employee-management screen (products/retail/frontend/employees.js), multi-device Phase 1, design §3.
  - `retail_financial_authority_test.py` — server-authoritative sale financial-authority regression suite (Wave 0, AUDIT-002/003/005/006/008/009).
  - `retail_metrics_by_employee_test.py` — takings per employee, from the v13 attribution column.
  - `retail_receipt_identity_parity_test.py` — receipt identity PARITY between the checkout response
  - `retail_registry_v3_accounts_test.py` — registry.db v3: the multi-device account model.
  - `retail_reports_by_employee_actor_lookup_test.py` — a failed actor lookup writes NULL, and must never do it in silence.
  - `retail_reports_by_employee_route_test.py` — GET /api/sub/retail/reports/by-employee, and the identical who rang this?" resolution on the sale-detail route.
  - `retail_reports_by_employee_shop_clock_test.py` — the dashboard's KPI card and its hourly chart must read ONE clock, and the shop clock must have a way to be set.
  - `retail_reports_capability_gate_test.js` — Retail — capability gating for the Reports surface.
  - `retail_route_capability_matrix_test.py` — capability gating on the retail API surface (Phase 1, design §3 "Permissions" / §7 step 1).
  - `retail_signout_unsynced_guard_test.js` — signing out must not silently strand completed sales on a till.
  - `retail_v13_identity_columns_migration_test.py` — schema v13 migration regression coverage (identity and attribution columns; ROADMAP.md's 2026-08-21 reservation, Phase 2 of…

## Sync & multi-device  (26 suites — 25 py, 1 js)
  - `_accept_branch_fallback_test.py` — ADVERSARIAL ACCEPTANCE PASS -- Phase 5 wave B1 (stock-moving sync).
  - `retail_branch_limit_test.py` — the `max_branches` licence entitlement (2026-09-05 price
  - `retail_branch_scope_enforcement_test.py` — branch-scope data-plane enforcement (launch-readiness account-hierarchy design §3.3/§4.2 D7, wave C2/Reading A).
  - `retail_branch_update_route_test.py` — PUT /api/sub/retail/branches/<id> (update_branch).
  - `retail_branches_test.js` — retail_branches_test.js — Branches screen (ci-hardening-w0.3 continuation, the doorway").
  - `retail_customer_sync_test.py` — multi-device sync foundation: Customers wiring.
  - `retail_device_branch_pin_test.py` — device-branch pinning: launch-readiness chain wave C1 (ROADMAP.md's 2026-08-30 "the multi-branch capture defect" entry…
  - `retail_drawer_terminal_scope_test.py` — Phase 4: the cash drawer belongs to a TERMINAL, and every
  - `retail_email_outbox_test.py` — email outbox foundation (feat/email-outbox-foundation).
  - `retail_import_sync_test.py` — bulk import must feed the multi-device sync outbox.
  - `retail_registry_v4_rebind_precount_race_test.py` — schema v14's half of Defect 3 (launch-readiness Phase 5 verification, MEDIUM): `database/schema.py::rebind_company_id` used to…
  - `retail_report_branch_filter_test.py` — branch-comparison reports regression suite (feat/reports-branch-comparison).
  - `retail_settings_sync_emit_test.py` — the EMIT side of shop-level settings sync (2026-09-05).
  - `retail_sync_conflicts_route_test.py` — the `sync_conflicts` READ route: launch-readiness 2026-08-29 the two exception queues both need ONE screen, not two" (ROADMAP.md).
  - `retail_sync_freshness_test.py` — schema v18 / SyncService regression coverage:
  - `retail_sync_inert_when_unconfigured_test.py` — multi-device sync foundation, Task 5 config-inertness
  - `retail_sync_relay_persisted_discovery_test.py` — launch-readiness (2026-09-03): SYNC_RELAY_BASE_URL
  - `retail_sync_relay_precedence_test.py` — launch-readiness (2026-09-03): SYNC_RELAY_BASE_URL
  - `retail_sync_relay_url_validation_test.py` — final-review Fix 2 (2026-08-07): scheme enforcement on
  - `retail_sync_starts_when_configured_test.py` — multi-device sync foundation, Task 5: the positive-path
  - `retail_sync_threshold_parity_test.py` — the staleness threshold exists twice, in two languages, and
  - `retail_v13_uid_index_hardening_test.py` — schema v13, the two ways the uid unique index can brick an install. See…
  - `retail_v13_uid_repair_termination_test.py` — schema v13: the duplicate-uid repair must TERMINATE, and must move only the rows it is there to move.
  - `retail_v14_company_rebind_migration_test.py` — schema v14 regression coverage: rebinding `company_id` from the locally-derived `md5(admin_email)` to the Owner-issued tenant key…
  - `retail_v14_owner_issued_company_id_shapes_test.py` — schema v14: `owner_issued_company_id()` must keep the promise its own docstring makes. See…
  - `retail_whatsapp_outbox_test.py` — WhatsApp report triggers (whatsapp-recipients feature).

## E-invoicing (Jordan / JoFotara)  (3 suites — 2 py, 1 js)
  - `retail_einvoicing_regression_test.py` — JoFotara e-invoicing (docs/einvoicing/phase1/) regression
  - `retail_einvoicing_status_test.js` — retail_einvoicing_status_test.js — the e-invoicing status card's "which provider is actually running" fix…
  - `retail_einvoicing_test.py` — JoFotara e-invoicing (docs/einvoicing/phase1/) integration

## Hardware — printing, drawer, display  (8 suites — 5 py, 3 js)
  - `retail_branding_receipt_test.js` — Aura Retail — the printed receipt is brandable (launch-readiness, make the system be brandable of whatever institute or coop or…
  - `retail_customer_display_test.js` — retail_customer_display_test.js — the customer-facing second screen (retail-hardware-viewports).
  - `retail_escpos_receipt_test.py` — core/retail/escpos_receipt.py -- pure byte-layer unit tests.
  - `retail_escpos_transport_test.py` — core/retail/escpos_transport.py -- Windows RAW print-spooler transport tests.
  - `retail_printer_kick_test.py` — cash-drawer kick on a completed sale (checkout, NOT settings).
  - `retail_printer_routes_test.py` — hardware (ESC/POS) receipt printer routes.
  - `retail_receipt_localization_test.js` — closes the seam between the i18n test suite (covers the live UI DOM) and the receipt/branding test suite (covers whether the…
  - `retail_receipt_payload_test.py` — GET /printer/receipt-payload, the real-sale ESC/POS byte payload (retail-hardware-viewports).

## Schema & migrations  (7 suites — 7 py, 0 js)
  - `retail_phase7_migration_test.py` — Phase 7 Part Y: rc.1 -> rc.2 migration safety.
  - `retail_po_split_migration_test.py` — schema v6 migration regression coverage (PO-preview-by- supplier foundation, Thursday demo Stream B -- see database/schema.py's…
  - `retail_v13_additive_only_behavioural_test.py` — the behavioural "additive only" guard for schema v13 and v14.
  - `retail_v14_migration_chain_wiring_test.py` — schema v14: is the migration STEP actually wired into the chain, in the right place, with its refusal caught? See…
  - `retail_v15_ledger_truth_migration_test.py` — schema v15 regression coverage: the ledger becomes able to reproduce the cache (launch-readiness Phase 3…
  - `retail_v17_catalogue_migration_test.py` — schema v17 regression coverage: launch-readiness Phase 6
  - `retail_v17_row_version_bump_test.py` — launch-readiness Phase 6 ("catalogue correctness"), stage

## Design, UI & i18n  (16 suites — 1 py, 15 js)
  - `retail_branding_test.py` — branding (launch-readiness, "make the system be brandable
  - `retail_btn_primary_contrast_test.js` — REGRESSION GUARD — `.ret-btn-primary` (and its siblings `.ret-btn-danger`, `.ret-btn-ghost`) must clear WCAG AA (4.5:1)…
  - `retail_currency_surface_test.js` — retail_currency_surface_test.js — every money surface must speak the SHOP's currency, including the ones no test was looking at.
  - `retail_dashboard_transaction_contrast_test.js` — Regression test for a WCAG AA contrast bug in the Retail dashboard's Recent Transactions" table.
  - `retail_design_contrast_test.js` — OPERATIONAL CALM — WCAG contrast, at BOTH the level the palette declares and the level the cashier actually sees.
  - `retail_design_css_parse_test.js` — THE STYLESHEET A BROWSER SEES, NOT THE ONE THE FILE LOOKS LIKE.
  - `retail_design_focus_test.js` — OPERATIONAL CALM — nothing is discoverable by hover alone.
  - `retail_design_render_test.js` — OPERATIONAL CALM — the RENDERED till, reconstructed, so the design tests can reason about what a cashier actually sees.
  - `retail_design_rtl_test.js` — OPERATIONAL CALM — RTL correct by construction, not by a chasing stylesheet.
  - `retail_design_theme_safety_test.js` — there are exactly FIVE sanctioned themes, and every path into every non-light one is validated the same way.
  - `retail_design_tokens_test.js` — OPERATIONAL CALM — the token layer is the ONLY place a colour is decided.
  - `retail_icons_test.js` — the icon-signature redesign (icons.js).
  - `retail_markup_emoji_test.js` — no raw emoji in rendered markup.
  - `retail_surface_dashboard_test.js` — retail_surface_dashboard_test.js — structural guards for the dashboard landing redesign ("Operational Calm").
  - `retail_surface_i18n_test.js` — retail_surface_i18n_test.js — every user-visible string on the reworked till and dashboard surfaces must exist in BOTH locale…
  - `retail_surface_pos_test.js` — retail_surface_pos_test.js — structural guards for the POS/till redesign ("Operational Calm").

## Reports & analytics  (7 suites — 4 py, 3 js)
  - `retail_dashboard_error_propagation_test.js` — Regression test for the Retail dashboard "silent failure + fake LIVE badge" bug.
  - `retail_dashboard_recent_date_test.js` — Regression test for a date-ambiguity bug in the Retail dashboard's Recent Transactions" table.
  - `retail_metrics_business_date_test.py` — report bucketing runs on the SHOP's business date, not on whichever device's wall clock happened to ring the sale.
  - `retail_metrics_consistency_test.py` — cross-screen revenue consistency suite.
  - `retail_report_clock_agreement_test.py` — every report route and the dashboard KPI must read ONE clock.
  - `retail_report_email_trigger_test.js` — retail_report_email_trigger_test.js — the on-demand "queue a summary report" control on the Email Notifications screen.
  - `retail_report_totals_fils_test.py` — the last four report figures (plus the accounting export) that were still quantized to 0.01 after the 2026-09-03 money-precision…

## Notifications & integrations  (4 suites — 3 py, 1 js)
  - `retail_email_notifications_test.js` — retail_email_notifications_test.js — Email Notifications screen (ci-hardening-w0.3 continuation, "the doorway", second one on…
  - `retail_email_verification_test.py` — email verification + forgot/reset password.
  - `retail_shift_close_email_test.py` — Closing a till must email the Z-report -- if, and only if, email is on.
  - `retail_whatsapp_nav_discoverability_test.py` — WhatsApp reports page discoverability regression.

## Backup, export & data safety  (3 suites — 2 py, 1 js)
  - `retail_backup_export_test.js` — retail_backup_export_test.js — Backup & Export screen (ci-hardening-w0.3 continuation, "the doorway", third one on this branch --…
  - `retail_backup_restore_test.py` — local backup/restore regression suite (Wave 0, AUDIT-019).
  - `retail_import_export_test.py` — import/export parity suite (Phase 2B).

## Other / cross-cutting  (38 suites — 22 py, 16 js)
  - `_accept_convergence_test.py` — ADVERSARIAL ACCEPTANCE PASS -- Phase 5 wave B1 (stock-moving sync).
  - `_accept_wedge_test.py` — ADVERSARIAL ACCEPTANCE PASS -- Phase 5 wave B1 (stock-moving sync).
  - `launcher_support_test.py` — commercial_runtime.launcher_support -- focused regression suite (Phase 3.7).
  - `retail_ai_language_test.py` — AI Assistant language-support suite (2026-08-13).
  - `retail_ai_rag_multitenant_test.py` — AI Assistant RAG multi-tenant isolation suite.
  - `retail_ai_streaming_test.py` — AI Assistant streaming suite (2026-08-13 speed pass).
  - `retail_ar_ap_totals_test.py` — AR/AP total_receivable / total_payable aggregation regression.
  - `retail_arabic_font_test.js` — THE ARABIC WEBFONT IS BUNDLED, DECLARED, AND ORDERED CORRECTLY -- OR A CUSTOMER-FACING SCREEN SILENTLY RENDERS BOXES OR FALLS…
  - `retail_audit_log_test.py` — feat/audit-log-viewer: real end-to-end coverage for GET /api/sub/retail/audit-log (products/retail/backend/api/retail_api.py).
  - `retail_audit_write_failure_test.py` — A failed audit write must be DISCOVERABLE without becoming FATAL.
  - `retail_btn_danger_hover_test.js` — Regression test for the `.ret-btn-danger` button class missing a `:hover` rule in the shared retail subsystem stylesheet.
  - `retail_business_day_route_test.py` — GET/POST /settings/business-day, against the real Flask app.
  - `retail_business_day_settings_test.js` — retail_business_day_settings_test.js — the Business Day card in Admin Center, and the one thing about it that is easy to get…
  - `retail_confirm_modal_test.js` — retail_confirm_modal_test.js — _confirm(), the token-styled replacement for native confirm() (owner brief, 2026-09-08: native…
  - `retail_currency_precision_test.py` — currency-aware money precision.
  - `retail_customer_modal_xss_test.js` — Regression test for a stored-XSS bug on the Retail Customers screen (products/retail/frontend/subsystem-retail.js).
  - `retail_drawer_screen_test.js` — retail_drawer_screen_test.js — the cash-drawer surface, rendered.
  - `retail_exceptions_screen_test.js` — Aura Retail — the EXCEPTIONS screen (launch-readiness 2026-08-29, "the two exception queues both need ONE screen, not two"…
  - `retail_handheld_nav_test.js` — retail_handheld_nav_test.js — the phone nav switch nobody was driving.
  - `retail_join_shop_modal_test.js` — retail_join_shop_modal_test.js — "join an existing shop" doorway on the setup modal…
  - `retail_localization_test.py` — localization / bilingual parity suite (Phase 2B).
  - `retail_nav_groups_test.js` — Retail — sidebar nav grouping (launch-readiness 2026-08-29).
  - `retail_offline_banner_24h_warning_test.js` — Phase 7 stage 7c-i — the 24-hour soft warning (docs/launch-readiness/phase7-offline-ux.md, "PART 2 -- the 24-hour soft warning").
  - `retail_onboarding_wave0_test.py` — first-run onboarding regression suite (Wave 0, AUDIT-001).
  - `retail_page_limit_clamp_test.py` — a page limit must be clamped at BOTH ends, for every spelling of the query value, not just the one an earlier test happened to…
  - `retail_po_number_uniqueness_test.py` — `purchase_orders.po_number` uniqueness and error
  - `retail_po_split_route_test.py` — route-level coverage for the PO-preview-by-supplier foundation (Thursday demo, Stream B): POST /purchase-orders/split-preview and…
  - `retail_po_split_unit_test.py` — unit tests for core/retail/po_split.py (PO-preview-by-supplier foundation, Thursday demo Stream B). Pure module tests, no Flask…
  - `retail_pos_keyboard_shortcuts_test.js` — retail_pos_keyboard_shortcuts_test.js — the POS keyboard-shortcut layer added so the till is usable at…
  - `retail_pos_name_xss_test.js` — Regression test for a stored-XSS bug on the Retail POS screen.
  - `retail_pos_scale_test.js` — retail_pos_scale_test.js — regression tests for "the POS scale fix (ROADMAP.md 2026-08-29 v21; see also GET /products/lookup and…
  - `retail_route_reachability_test.py` — every route a customer needs must have a doorway.
  - `retail_security_test.py` — security regression suite.
  - `retail_shell_chrome_test.js` — retail_shell_chrome_test.js — the desktop shell's "corners" (owner, 2026-09-07, looking at the running till): "the interface has…
  - `retail_toast_tokens_test.js` — retail_toast_tokens_test.js — the JS-BUILT surfaces follow the theme.
  - `retail_tombstone_test.py` — multi-device sync: tombstones (launch-readiness Phase 6
  - `retail_two_install_roundtrip_test.py` — multi-device sync: two-install composition harness.
  - `wave1c_financial_gate_test.py` — Wave 1C release-gate re-audit: independent financial-authority regression suite.
