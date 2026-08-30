package com.actionaura.retail.net

import com.google.gson.annotations.SerializedName

// Request/response models for the local Flask API. Fields are nullable/defaulted
// so partial JSON never crashes parsing.

data class LoginRequest(val email: String, val password: String)

data class OnboardingStatus(val needs_setup: Boolean = false)

data class CreateAdminRequest(
    val name: String,
    val email: String,
    val password: String,
    val company_name: String = "",
)

data class CreateAdminResponse(
    val success: Boolean = false,
    val error: String? = null,
    val user: User? = null,
)

data class User(
    val id: String? = null,
    val email: String? = null,
    val role: String? = null,
    val clinic_role: String? = null,
    val employee_id: String? = null,
    /**
     * This account's own `retail.*` capability grants -- as a FALLBACK ONLY.
     *
     * The live contract puts them at the TOP LEVEL of the session body, beside
     * `user` and never inside it (see [SessionResponse.capabilities]). This
     * field stays because the clinic product shares this identity stack on a
     * separate code path, and because `user` is the obvious place for a future
     * contributor to add the key -- reading both costs one null check and
     * removes a failure mode whose whole character is that it is silent. It is
     * NOT where the retail session's grants arrive; `ui.sessionCapabilities`
     * owns the resolution and prefers the top-level key.
     *
     * Nullable ON PURPOSE, and null is the meaningful default: "absent" has to
     * stay distinguishable from "granted nothing". `ui.holdsCapability`
     * documents why that distinction decides between an inert change and one
     * that blanks a screen for every role the day it ships.
     *
     * The Gson explicit-null hazard that crashed EmployeesScreen does not bite
     * here for the same reason: the field is declared nullable, so a server
     * that sends `"capabilities": null` produces exactly the value the type
     * already admits, and the null is handled instead of being assigned into
     * a field Kotlin promised could not hold one.
     */
    val capabilities: List<String>? = null,
)

data class LoginResponse(
    val success: Boolean = false,
    val is_mt: Boolean = false,
    val error: String? = null,
    val require_password_change: Boolean = false,
    val user: User? = null,
)

data class SessionResponse(
    val authenticated: Boolean = false,
    val language: String? = null,
    val user: User? = null,
    /**
     * Where `/api/auth/session` ACTUALLY puts this account's `retail.*` grants:
     * a TOP-LEVEL key, a sibling of `user`, never a member of it. Read
     * commercial_runtime/identity/onboarding_routes.py::get_session -- the
     * jsonify literal emits `'capabilities': capabilities` and then opens
     * `'user': {...}` as a separate dict.
     *
     * This field is new, and its absence was the entire bug. The client
     * declared `capabilities` on [User] only, so parsing a real cashier body
     * left the list null, and `hasCapability` -- which fails open on null,
     * correctly and by design -- answered TRUE for an account granted only
     * sell, refund and cash.close. Every capability gate on this client was
     * therefore inert from the day it shipped, and nothing was red, because
     * "a gate that never engages is indistinguishable from a gate on a
     * permissive account" (app-shell.js:272, the desktop shell's dated
     * post-mortem for the identical bug in the identical place).
     *
     * Nullable for the same reason [User.capabilities] is: an absent key and
     * an explicit JSON null both mean "not known", and an empty list means
     * "this account holds nothing". Those are three different answers and
     * SessionCapabilityContractTest keeps them apart.
     */
    val capabilities: List<String>? = null,
)

// ── This device's own identity in the device registry ────────────────────────
/**
 * One row of `device_registry.devices`, as
 * `commercial_runtime/identity/device_routes.py::_serialize_device` returns it.
 *
 * Only `id` is load-bearing here, and it is the value the backend stamps into
 * `sales.terminal_id` / `returns.terminal_id` / `inventory_movements.terminal_id`
 * for every write this device makes -- `database/schema.py::local_terminal_id()`
 * returns the same install UUID this row is keyed by, on purpose, so that
 * "which till rang this?" can be answered by joining the two. The rest of the
 * columns are carried because the route sends them and dropping fields from a
 * model is how the next reader concludes the server never sent them.
 */
data class DeviceRow(
    val id: String? = null,
    val company_id: String? = null,
    val device_label: String? = null,
    val platform: String? = null,
    val device_fingerprint: String? = null,
    val is_admin_device: Boolean = false,
    val status: String? = null,
    val first_seen_at: String? = null,
    val last_seen_at: String? = null,
    val owner_installation_id: String? = null,
)

/**
 * `GET /api/devices/me`. `device` is nullable for the same Gson reason
 * [ByEmployeeResponse.data] is: an explicit JSON null is written straight into
 * a non-primitive field whatever the Kotlin type claims, so the type has to
 * admit it or the NullPointerException lands later and outside the try/catch.
 */
data class MyDeviceResponse(
    val success: Boolean = false,
    val device: DeviceRow? = null,
    val can_claim_admin: Boolean = false,
    val error: String? = null,
    val code: String? = null,
)

// Clinic dashboard stats: { status, data: {...} }
data class ClinicStats(
    val today_appointments: Int = 0,
    val waiting: Int = 0,
    val active_visits: Int = 0,
    val today_revenue: Double = 0.0,
    val total_patients: Int = 0,
)

data class ClinicStatsResponse(
    val status: String = "",
    val data: ClinicStats? = null,
)

// ── Patients ──────────────────────────────────────────────────────────────────
data class Patient(
    val id: Int = 0,
    val patient_code: String? = null,
    val name: String? = null,
    val gender: String? = null,
    val dob: String? = null,
    val phone: String? = null,
    val email: String? = null,
    val blood_type: String? = null,
    val status: String? = null,
    val notes: String? = null,
)

data class PatientsResponse(val status: String = "", val data: List<Patient> = emptyList())

data class CreatePatientRequest(
    val name: String,
    val gender: String? = null,
    val dob: String? = null,
    val phone: String? = null,
    val email: String? = null,
    val blood_type: String? = null,
    val address: String? = null,
    val notes: String? = null,
)

data class CreatedRow(val id: Int = 0, val patient_code: String? = null)
data class CreatedResponse(val status: String = "", val message: String? = null, val data: CreatedRow? = null)

// Retail entities moved off autoincrement INTEGER ids to client-generated
// UUID TEXT ids server-side (multi-device sync foundation, 2026-08-06 --
// see products/retail/backend/database/schema.py's _migrate_products_to_uuid
// / _migrate_customers_to_uuid / _migrate_suppliers_to_uuid, all gated behind
// RETAIL_SCHEMA_VERSION 7). CreatedRow.id: Int is still correct for Clinic's
// create* endpoints (patient/appointment/invoice/payment/lab-expense keep
// integer ids), so it can't be widened in place: Gson's GsonConverterFactory
// throws NumberFormatException trying to read a UUID string into an Int
// field, which surfaced to the user as a false "Couldn't reach the server"
// on Add Product -- even though the product had already been committed
// server-side (product create is commit-then-respond; only parsing the
// response failed). Root-caused on the physical demo device 2026-08-12,
// first repro immediately after real license activation succeeded --
// coincidence of timing, not cause: this was the first product-create
// attempt against the already-migrated (v7) schema, not a sync/licensing
// interaction. createCategory/createCustomer/createSupplier return the same
// UUID-shaped `data.id` and hit the identical failure the first time each is
// exercised; routed through this same type for all four.
data class CreatedRowId(val id: String? = null)
data class CreatedIdResponse(val status: String = "", val message: String? = null, val data: CreatedRowId? = null)

// ── Visits / Appointments / Prescriptions / Invoices ─────────────────────────
data class Visit(
    val id: Int = 0, val doctor_id: Int? = null, val status: String? = null,
    val diagnosis: String? = null, val created_at: String? = null,
)
data class Appointment(
    val id: Int = 0, val patient_id: Int? = null, val patient_name: String? = null,
    val patient_phone: String? = null, val doctor_id: Int? = null,
    val appointment_dt: String? = null, val reason: String? = null, val status: String? = null,
)
data class AppointmentsResponse(val status: String = "", val data: List<Appointment> = emptyList())

data class Prescription(
    val id: Int = 0, val patient_id: Int? = null, val items_json: String? = null,
    val notes: String? = null, val created_at: String? = null,
)
data class PrescriptionsResponse(val status: String = "", val data: List<Prescription> = emptyList())

data class Invoice(
    val id: Int = 0, val invoice_number: String? = null, val patient_id: Int? = null,
    val patient_name: String? = null, val total: Double = 0.0, val amount_paid: Double = 0.0,
    val status: String? = null, val created_at: String? = null,
)
data class InvoicesResponse(val status: String = "", val data: List<Invoice> = emptyList())

data class PatientDetail(
    val patient: Patient? = null,
    val visits: List<Visit> = emptyList(),
    val appointments: List<Appointment> = emptyList(),
)
data class PatientDetailResponse(val status: String = "", val data: PatientDetail? = null)

// ── Doctors / Services (for booking + invoicing) ─────────────────────────────
data class Doctor(val id: Int = 0, val name: String? = null, val specialty: String? = null)
data class DoctorsResponse(val status: String = "", val data: List<Doctor> = emptyList())
data class Service(val id: Int = 0, val name: String? = null, val price: Double = 0.0)
data class ServicesResponse(val status: String = "", val data: List<Service> = emptyList())

// ── Clinic create requests ───────────────────────────────────────────────────
data class CreateAppointmentRequest(
    val patient_id: Int, val doctor_id: Int?, val appointment_dt: String, val reason: String = "",
)
data class InvoiceItemReq(val description: String, val qty: Double = 1.0, val unit_price: Double = 0.0)
data class CreateInvoiceRequest(
    val patient_id: Int, val items: List<InvoiceItemReq>,
    val discount: Double = 0.0, val tax_rate: Double = 0.0, val notes: String = "",
)
data class CreatePaymentRequest(val invoice_id: Int, val amount: Double, val method: String = "cash")

// ── Lab expenses ──────────────────────────────────────────────────────────────
data class LabExpense(
    val id: Int = 0, val lab_name: String? = null, val test_name: String? = null,
    val amount: Double = 0.0, val expense_date: String? = null, val patient_name: String? = null,
)
data class LabExpensesResponse(val status: String = "", val data: List<LabExpense> = emptyList())
data class CreateLabExpenseRequest(
    val lab_name: String, val test_name: String = "", val amount: Double = 0.0,
    val expense_date: String? = null, val patient_id: Int? = null,
)

// ── Retail (POS) ──────────────────────────────────────────────────────────────
// id: String, not Int -- products.id is a client-generated UUID TEXT primary
// key server-side (multi-device sync foundation, RETAIL_SCHEMA_VERSION 7 /
// _migrate_products_to_uuid). See CreatedIdResponse's doc comment for the
// full story: this mismatch is what broke "Add Product" (create response)
// AND the Products list / POS cart / checkout / receipts (every response
// that carries a product id), first surfaced physically 2026-08-12.
data class Product(
    val id: String = "", val sku: String? = null, val name: String? = null,
    val sell_price: Double = 0.0, val cost_price: Double = 0.0, val tax_rate: Double = 0.0,
    val total_stock: Double = 0.0, val reorder_level: Int = 0, val unit: String? = null,
    val category_name: String? = null,
    val barcode: String? = null,   // returned by the Flask API; used for camera-scan lookup
)
data class ProductsResponse(val status: String = "", val data: List<Product> = emptyList())

// Single-product resolve (GET /products/lookup?code=) -- the AuraApi.productLookup()
// success envelope. A 404 (no match) never reaches this class at all: Retrofit
// throws retrofit2.HttpException before any body here gets deserialized, which is
// exactly how barcode/ProductLookup.kt's lookupProductByCode tells "not found"
// apart from every other failure. `data` is still nullable/defaulted (never trust
// a 200 to carry a body) rather than assumed non-null on the strength of the
// status code alone.
data class ProductLookupResponse(val status: String = "", val data: Product? = null)

// Categories -- multi-device sync foundation (2026-08-06), Task 9 wiring:
// GET/POST /api/sub/retail/categories, PUT /categories/{id}. Category
// create/update is the only entity type Task 4's sync_outbox wiring
// understands (see commercial_runtime/sync/sync_service.py's own docstring)
// -- this is a real, working management screen (CategoriesScreen.kt), not
// a stub, added specifically so a category change can be made through this
// app's real UI and verified end to end against Owner's relay.
data class Category(
    val id: String = "", val name: String = "", val description: String? = "",
    val product_count: Int = 0,
)
data class CategoriesResponse(val status: String = "", val data: List<Category> = emptyList())
data class CreateCategoryRequest(val name: String, val description: String = "")

data class CreateProductRequest(
    val name: String, val sku: String,
    val sell_price: Double = 0.0, val cost_price: Double = 0.0,
    val tax_rate: Double = 0.0, val reorder_level: Int = 5,
    val initial_stock: Double = 0.0, val barcode: String = "", val unit: String = "pcs",
)

// Edit an existing product (PATCH /products/{id}). All fields are sent with real
// values prefilled from the product, so nothing is accidentally nulled out.
data class UpdateProductRequest(
    val name: String, val sell_price: Double, val cost_price: Double,
    val tax_rate: Double, val reorder_level: Int, val barcode: String = "", val unit: String = "pcs",
)
// Stock adjustment (POST /products/{id}/stock-adjust). quantity: + to add, − to remove.
data class AdjustStockRequest(val quantity: Double, val reason: String = "Manual correction")
data class StockAdjustResponse(val status: String = "", val new_stock: Double = 0.0, val message: String? = null)

// Commercial intent only (Wave 0 / Phase 4D financial-authority contract --
// see docs/architecture/financial-authority-contracts.md). product_id and
// quantity are the only per-line fields the server accepts as intent;
// discount_pct is the only other client-settable per-line field, and there
// is no discount-entry UI in this source yet (NOT PRESENT IN SOURCE -- not
// added in this migration phase), so it always defaults to 0 here.
// unit_price/tax_rate/line_total are never sent -- the server always
// resolves them from the product row, never from the request body.
data class SaleItemReq(val product_id: String, val quantity: Double, val discount_pct: Double = 0.0)

// subtotal/discount_amount/tax_amount/total are deliberately NOT fields
// here (Wave 0: the server computes and ignores any client-submitted
// values for these -- sending them at all would misleadingly imply the
// client has a say in them). amount_paid is legitimate tender input, but
// nullable: the client cannot know the tax-inclusive authoritative total
// in advance (that's the whole point of server-side tax resolution), so a
// "pay in full" cash/card/etc. sale must omit amount_paid entirely and let
// the server default it to the computed total (retail_api.py:
// `data.get('amount_paid', total)`) -- sending a client-guessed pre-tax
// amount here (a real Wave 1A device bug, MOB-001) caused the server to
// see an underpayment and reject the sale as an implicit credit sale
// requiring a customer, on every taxed product. Only an explicit partial
// down-payment (credit sales) should ever populate this field.
data class CreateSaleRequest(
    val amount_paid: Double? = null, val payment_method: String = "cash",
    val items: List<SaleItemReq>, val idempotency_key: String,
    val customer_id: String? = null, val due_date: String? = null,
)

// Mirrors the authoritative response contract in
// docs/architecture/financial-authority-contracts.md -- every financial
// field here is server-computed; the client must display these values
// verbatim, never a locally-computed equivalent.
data class SaleResult(
    val id: Int = 0, val sale_number: String? = null,
    val subtotal: Double = 0.0, val discount_amount: Double = 0.0,
    val tax_amount: Double = 0.0, val total: Double = 0.0,
    val amount_paid: Double = 0.0, val change: Double = 0.0,
    val balance_due: Double = 0.0, val warning: String? = null,
    val calculation_version: String? = null,
)
data class SaleResponse(val status: String = "", val message: String? = null, val data: SaleResult? = null)

// ── Retail customers + credit (Accounts Receivable) ──────────────────────────
// id: String -- customers.id is also a UUID TEXT primary key (same
// migration/rationale as Product.id above; see _migrate_customers_to_uuid).
data class Customer(
    val id: String = "", val name: String? = null, val phone: String? = null, val email: String? = null,
    val credit_mode: String? = "none", val credit_limit: Double = 0.0, val credit_balance: Double = 0.0,
)
data class CustomersResponse(val status: String = "", val data: List<Customer> = emptyList())
data class ReceivablesResponse(val status: String = "", val total_receivable: Double = 0.0, val data: List<Customer> = emptyList())
data class CreateCustomerRequest(val name: String, val phone: String = "", val email: String = "", val address: String = "")
data class UpdateCustomerCreditRequest(val credit_mode: String, val credit_limit: Double)

data class StatementEvent(val ref: String? = null, val date: String? = null, val kind: String? = null,
    val amount: Double = 0.0, val running_balance: Double = 0.0, val payment_id: Int? = null)
data class CustomerStatement(val customer: Customer? = null, val events: List<StatementEvent> = emptyList(), val balance: Double = 0.0)
data class CustomerStatementResponse(val status: String = "", val data: CustomerStatement? = null)

data class PaymentRequest(val amount: Double, val method: String = "cash", val notes: String = "")
data class PaymentResult(val reference: String? = null, val new_balance: Double = 0.0)
data class PaymentResultResponse(val status: String = "", val message: String? = null, val data: PaymentResult? = null)

data class RetailStats(
    val today_sales: Double = 0.0, val today_transactions: Int = 0,
    val total_products: Int = 0,
    // Backend sends this key as "low_stock_alerts"; map it so the KPI isn't always 0.
    @SerializedName("low_stock_alerts") val low_stock: Int = 0,
)
data class RetailStatsResponse(val status: String = "", val data: RetailStats? = null)

// ── Retail: sales history (transactions / invoices) ──────────────────────────
data class Sale(
    val id: Int = 0, val sale_number: String? = null,
    val total: Double = 0.0, val subtotal: Double = 0.0,
    val tax_amount: Double = 0.0, val discount_amount: Double = 0.0,
    val amount_paid: Double = 0.0, val change_amount: Double = 0.0,
    val payment_method: String? = null, val status: String? = null,
    val customer_name: String? = null, val item_count: Int = 0,
    val created_at: String? = null,
    // ── Who rang it (retail schema v13) ──────────────────────────────────────
    // `actor_user_uid` is the column v13 added to `sales`, and it is NULL on
    // every row that predates the migration -- deliberately, because "this
    // device cannot prove it is the terminal that rang a sale from before the
    // column existed" (see _migrate_add_identity_and_attribution_columns).
    // `actor_employee_id` / `actor_email` are that uid RESOLVED against the
    // registry server-side; they are absent when the account has since been
    // removed, which is a different fact from never having been recorded --
    // ui/screens/EmployeeSalesScreen.kt::saleAttribution keeps the two apart.
    //
    // There is deliberately NO `cashier` field, even though `sales.cashier`
    // exists and v13 keeps it. That column's schema default is the literal
    // 'POS' and create_sale writes `session['mt_user_id']` into it, so it
    // holds a placeholder or an opaque account id -- never a name. v13 keeps
    // it because it is the only surviving evidence of who the shop BELIEVED
    // rang a transaction, and refuses to read it to guess an actor; a field
    // here would be a standing invitation to print it next to "Rung by".
    val actor_user_uid: String? = null,
    val actor_employee_id: String? = null,
    val actor_email: String? = null,
)
data class SalesResponse(val status: String = "", val data: List<Sale> = emptyList())

data class SaleLine(
    val product_id: String = "",
    val product_name: String? = null, val sku: String? = null,
    val quantity: Double = 0.0, val unit_price: Double = 0.0, val line_total: Double = 0.0,
)
data class SaleDetail(val sale: Sale? = null, val items: List<SaleLine> = emptyList())
data class SaleDetailResponse(val status: String = "", val data: SaleDetail? = null)

// ── Retail: suppliers ────────────────────────────────────────────────────────
// id: String -- suppliers.id is also a UUID TEXT primary key (same
// migration/rationale as Product.id above; see _migrate_suppliers_to_uuid).
data class Supplier(
    val id: String = "", val name: String? = null, val phone: String? = null,
    val email: String? = null, val address: String? = null, val order_count: Int = 0,
    val payment_terms: String? = "none", val credit_balance: Double = 0.0,
)
data class SuppliersResponse(val status: String = "", val data: List<Supplier> = emptyList())
data class CreateSupplierRequest(
    val name: String, val phone: String = "", val email: String = "", val address: String = "",
)

// ── Retail: purchase orders ──────────────────────────────────────────────────
data class PurchaseOrder(
    val id: Int = 0, val po_number: String? = null, val supplier_name: String? = null,
    val status: String? = null, val total: Double = 0.0,
    val ordered_at: String? = null, val received_at: String? = null, val notes: String? = null,
    val amount_paid: Double = 0.0, val payment_status: String? = "unpaid",
)
data class PurchaseOrdersResponse(val status: String = "", val data: List<PurchaseOrder> = emptyList())

data class PoLine(
    val product_name: String? = null, val sku: String? = null,
    val quantity: Double = 0.0, val unit_cost: Double = 0.0,
    val total: Double = 0.0, val received_qty: Double = 0.0,
)
data class PoDetail(val po: PurchaseOrder? = null, val items: List<PoLine> = emptyList())
data class PoDetailResponse(val status: String = "", val data: PoDetail? = null)

data class PoItemReq(val product_id: String, val quantity: Double, val unit_cost: Double)
data class CreatePoRequest(val supplier_id: String, val notes: String = "", val items: List<PoItemReq>,
    val amount_paid: Double = 0.0)

// ── Retail: supplier AP, daily cash, settings, payment methods (Phase 3) ─────
data class PayablesResponse(val status: String = "", val total_payable: Double = 0.0, val data: List<Supplier> = emptyList())
data class SupplierStatement(val supplier: Supplier? = null, val events: List<StatementEvent> = emptyList(), val balance: Double = 0.0)
data class SupplierStatementResponse(val status: String = "", val data: SupplierStatement? = null)

data class CreditSettings(
    val base_currency: String = "USD",
    val default_credit_mode: String = "none",
    val default_credit_limit: String = "0",
    val enforce_credit_limit: String = "warn",
)
data class CreditSettingsResponse(val status: String = "", val data: CreditSettings? = null)

data class PayMethod(val id: Int = 0, val name: String? = null, val type: String? = null,
    val is_active: Int = 1, val sort_order: Int = 0)
data class PayMethodsResponse(val status: String = "", val data: List<PayMethod> = emptyList())
data class CreatePayMethodRequest(val name: String, val type: String = "other")

data class CashMethodRow(val direction: String? = null, val method: String? = null,
    val amount: Double = 0.0, val count: Int = 0)
data class DailyCash(val date: String? = null, val cash_in: Double = 0.0, val cash_out: Double = 0.0,
    val net: Double = 0.0, val by_method: List<CashMethodRow> = emptyList())
data class DailyCashResponse(val status: String = "", val data: DailyCash? = null)

// Aging buckets (JSON keys "1_30" etc. aren't valid Kotlin identifiers → @SerializedName)
data class AgingBuckets(
    val current: Double = 0.0,
    @SerializedName("1_30") val d1_30: Double = 0.0,
    @SerializedName("31_60") val d31_60: Double = 0.0,
    @SerializedName("61_90") val d61_90: Double = 0.0,
    @SerializedName("90_plus") val d90_plus: Double = 0.0,
)
data class AgingResponse(val status: String = "", val type: String = "", val data: AgingBuckets? = null)

// ── Retail: branches + this device's branch pin (Wave C1) ────────────────────
// The dc22b04 fix (see retail_api.py's `_resolve_working_branch`) resolves
// every write's branch as: explicit request branch_id -> THIS DEVICE'S pinned
// branch_uid -> `_default_branch`. This client sends no `branch_id` anywhere
// (grep confirms it), so on a chain sharing one licence -- where sync
// converges every branch row onto every device -- an unpinned Android till
// silently falls through to the company's default branch and every sale it
// rings is filed there. This is the only place that pin can be set from this
// app; see ui/screens/SettingsScreen.kt.

// One row of `branches`, as GET /branches returns it (`dict(r) for r in
// rows`, retail_api.py::list_branches). `id`: Int, NOT a UUID-migrated entity
// like Product/Customer/Supplier (branches never went through that
// migration), so there is no Gson NumberFormatException risk here. `uid` is
// the cross-device identity POST /device/branch actually pins -- `id` is
// local-database-only and this screen never sends it back.
data class Branch(
    val id: Int = 0, val uid: String? = null, val name: String? = null,
    val address: String? = null, val phone: String? = null, val status: String? = null,
)
data class BranchesResponse(val status: String = "", val data: List<Branch> = emptyList())

// GET/POST /api/sub/retail/device/branch's shared payload shape
// (retail_api.py::get_device_branch/set_device_branch). `branch_uid` null
// means unpinned -- the state that produces silently wrong data on a chain,
// see SettingsScreen.kt for how that is surfaced. `branch_id`/`branch_name`
// are that uid resolved AGAINST THIS DEVICE'S OWN branches table -- both null
// when unpinned, and also both null (with `branch_uid` still non-null) when
// the pin does not resolve locally yet. The route documents this
// deliberately: a read must not self-heal a branch as a side effect of
// merely loading a screen, so a pin this device hasn't synced yet must still
// be reported as pinned, not shown as unpinned.
data class DeviceBranch(
    val branch_uid: String? = null, val branch_id: Int? = null, val branch_name: String? = null,
)
// `data` nullable for the same Gson-explicit-null reason every other response
// model in this file is (see CreatedResponse/ByEmployeeResponse's doc
// comments) -- a malformed 200 must fail the same way every other failure
// does, not crash past this screen's try/catch.
data class DeviceBranchResponse(val status: String = "", val message: String? = null, val data: DeviceBranch? = null)

// POST body. `branch_uid` null (or, after the server's own strip, an empty
// string) clears the pin -- set_device_branch's own contract. An unknown uid
// for this company comes back a 400, never silently ignored.
data class SetDeviceBranchRequest(val branch_uid: String? = null)

// ── Retail: reports (period-selectable stats) ────────────────────────────────
// NOTE: these endpoints wrap their payload as {"success": true, "data": ...}.
data class ReportSummary(
    val period_days: Int = 0,
    val revenue: Double = 0.0, val prev_revenue: Double = 0.0, val revenue_change: Double = 0.0,
    val transactions: Int = 0, val avg_ticket: Double = 0.0,
    val cogs: Double = 0.0, val gross_profit: Double = 0.0, val margin_pct: Double = 0.0,
    val inventory_value: Double = 0.0,
)
data class ReportSummaryResponse(val success: Boolean = false, val data: ReportSummary? = null)

data class PaymentMethodStat(val payment_method: String? = null, val count: Int = 0, val revenue: Double = 0.0)
data class PaymentMethodsResponse(val success: Boolean = false, val data: List<PaymentMethodStat> = emptyList())

/**
 * One row of GET /api/sub/retail/reports/by-employee -- takings and
 * transaction count for one person over the chosen period.
 *
 * `actor_user_uid` is `sales.actor_user_uid` (retail schema v13). Rows where
 * it is null are the UNATTRIBUTED bucket: every sale rung before the column
 * existed, aggregated together so the rows still sum to what
 * /reports/summary reports for the same period. Dropping that bucket would
 * make the report quietly disagree with every other number in the app; naming
 * anybody for it would be the fabrication v13 refused to commit.
 *
 * `employee_id` / `email` are the uid resolved against the registry
 * server-side. Both absent while `actor_user_uid` is present means the account
 * has been removed since -- attributed, but no longer resolvable. See
 * ui/screens/EmployeeSalesScreen.kt::rowAttribution.
 */
data class EmployeeSales(
    val actor_user_uid: String? = null,
    val employee_id: String? = null,
    val email: String? = null,
    val revenue: Double = 0.0,
    val transactions: Int = 0,
    val avg_ticket: Double = 0.0,
)

/**
 * `data` is NULLABLE and that is the entire point.
 *
 * Gson (converter-gson 2.11.0) builds a Kotlin data class whose parameters all
 * carry defaults through the synthetic no-arg constructor -- so a field
 * declared `= emptyList()` really does start as an empty list -- and then
 * ReflectiveTypeAdapterFactory writes an explicit JSON null straight into it,
 * because the field is not primitive. Kotlin's non-null type is a compile-time
 * claim that nothing enforces at the field level, so the assignment succeeds
 * silently and the NullPointerException lands LATER, at the first `.isEmpty()`
 * or iteration, OUTSIDE whatever try/catch wrapped the call. That is an
 * uncaught crash rather than an error state, and this module has already paid
 * for it once (EmployeesScreen.kt::load carries the post-mortem).
 *
 * Declaring it nullable puts the truth back in the type: the load path cannot
 * walk past it, and the malformed 200 takes the same error route as every
 * other failure instead of being rendered as "nobody sold anything".
 */
data class ByEmployeeResponse(
    /**
     * The DESKTOP discriminator, and the one that wins when both are present.
     *
     * `/reports/by-employee` answers with both spellings on purpose: the five
     * sibling chart/KPI report routes (`sales-trend`, `top-products`,
     * `payment-methods`, `summary`, `by-branch`) return `{"success": true}`
     * and carry no `status` at all, while the rest of retail_api.py -- and
     * subsystem-retail.js's hard gate on this very panel -- uses
     * `{"status": "success"}`. Carrying both is what lets the desktop and this
     * client read the same body unchanged.
     *
     * Nullable, not `""`: a blank or absent `status` is not a verdict, and
     * treating it as "not success" would refuse every reply from a server that
     * settled on the sibling spelling.
     */
    val status: String? = null,
    val success: Boolean = false,
    val data: List<EmployeeSales>? = null,
    /**
     * The two error spellings this backend uses --
     * `{"status":"error","message":...}` and `{"success":false,"error":...}`.
     * net/ApiErrors.kt already decodes both for HTTP failures; a 200 that
     * carries a refusal has to be decodable through the same two, or the
     * reason reaches the user as a blank line.
     */
    val message: String? = null,
    val error: String? = null,
)

// ── Retail: returns / refunds ────────────────────────────────────────────────
data class Return(
    val id: Int = 0, val return_number: String? = null, val sale_number: String? = null,
    val customer_name: String? = null, val reason: String? = null,
    val refund_method: String? = null, val refund_amount: Double = 0.0, val created_at: String? = null,
)
data class ReturnsResponse(val status: String = "", val data: List<Return> = emptyList())

// Commercial intent only: product_id + quantity per line. unit_price/
// line_total are never sent -- the server always recomputes the refund
// from the ORIGINAL sale_items row for that sale+product (Wave 0,
// AUDIT-004), proportional to the quantity being returned, tax-inclusive.
// See docs/corrections/wave0/retail-return-correction.md.
data class ReturnItemReq(val product_id: String, val quantity: Double)
data class CreateReturnRequest(
    val sale_id: Int, val reason: String = "Customer return",
    val refund_method: String = "cash", val items: List<ReturnItemReq>,
    // Required for real duplicate-submission protection (a double-tap on
    // "Process refund" must not create two returns) -- the source app
    // never generated one at all, so no return request was ever
    // deduplicated client-side prior to this phase.
    val idempotency_key: String,
)

// ── Aura AI chat (retail_api.py::ai_chat) ────────────────────────────────────
// Request matched to what the route actually reads: `message` (required;
// server trims and caps at 4000 chars), `history` = the PRIOR [{role,
// content}] turns (the server keeps only the last 10 and truncates each, so
// the client just sends everything it has), and `lang` ('en'/'ar',
// whitelisted server-side via _resolve_ai_language()). `stream` is
// deliberately absent: the route only switches to NDJSON when `stream` is a
// literal JSON true ("strict identity check, not truthiness" in the route),
// and the one-shot JSON shape below is the only one this Gson model parses.
data class AiChatTurn(val role: String, val content: String)
data class AiChatRequest(
    val message: String,
    val history: List<AiChatTurn> = emptyList(),
    val lang: String = "en",
)
// 200 response: {"success": true, "data": {"reply": "..."}}. The route's
// 400/503 bodies ({"success": false, "error": "..."}) never reach this
// model -- Retrofit raises HttpException for non-2xx, which ApiErrors.kt
// decodes (surfacing that same "error" text).
data class AiChatReply(val reply: String? = null)
data class AiChatResponse(val success: Boolean = false, val error: String? = null, val data: AiChatReply? = null)

// Server-computed, tax-inclusive refund breakdown -- mirrors
// docs/architecture/financial-authority-contracts.md's Retail return
// contract. The client must display refund_amount from here, never a
// locally-summed estimate, once the return has actually been submitted.
//
// product_id: String, NOT Int -- this is the same "Int field vs UUID string"
// class of bug documented above (line 91) for CreatedRowId, just never
// applied to this sibling field. create_return() (retail_api.py) echoes
// back the client-generated UUID product_id verbatim from ReturnItemReq
// (return_items.product_id is TEXT since RETAIL_SCHEMA_VERSION 7), so the
// non-lenient GsonConverterFactory (ApiClient.kt) throws
// NumberFormatException parsing that UUID string into an Int here -- the
// return has already been committed server-side (row inserted, inventory
// already adjusted) when this parse failure surfaces to the user as a false
// "Couldn't reach the server". Worse than the CreatedRowId case: the
// idempotency_key in RetailExtraScreens.kt's "Process refund" handler is
// regenerated fresh via UUID.randomUUID().toString() on every button press
// rather than cached across retry attempts, so a user retrying after this
// false failure creates a genuine second return/refund -- double-crediting
// the customer and double-adjusting inventory, not just a UI glitch.
data class ReturnItemResult(
    val product_id: String = "", val quantity: Double = 0.0, val unit_price: Double = 0.0,
    val discount_amount: Double = 0.0, val tax_amount: Double = 0.0, val line_total: Double = 0.0,
)
data class ReturnResult(
    val id: Int = 0, val return_number: String? = null,
    val refund_amount: Double = 0.0, val idempotency_key: String? = null,
    val items: List<ReturnItemResult> = emptyList(), val calculation_version: String? = null,
)
data class CreateReturnResponse(val status: String = "", val message: String? = null, val data: ReturnResult? = null)

// ── Backup / restore (Wave 1A, Part G) ──────────────────────────────────────
// Mirrors commercial_runtime/backup/routes.py exactly -- admin-only backend
// endpoints (Wave 0 / Phase 3.7), unchanged by this UI. Note `status` here
// is "ok"/"error" (not "success"/"error" like the rest of this API).
data class BackupManifest(
    val product_code: String? = null, val schema_version: Int = 0,
    val app_version: String? = null, val created_at: String? = null,
)
data class CreateBackupResponse(
    val status: String = "", val message: String? = null,
    val filename: String? = null, val manifest: BackupManifest? = null,
)
data class BackupEntry(val filename: String = "", val size: Long = 0, val modified_at: Double = 0.0)
data class ListBackupsResponse(val status: String = "", val message: String? = null, val backups: List<BackupEntry> = emptyList())
data class RestoreBackupRequest(val filename: String)
data class RestoreBackupResponse(
    val status: String = "", val message: String? = null,
    val restored: List<String> = emptyList(), val rollback_dir: String? = null,
)

// ── Employees (Phase 1 -- multi-device account model, design doc §3) ─────────
// Mirrors commercial_runtime/identity/onboarding_routes.py's /api/admin/*
// employee surface. That blueprint answers {"success": ...} / {"error": ...},
// NOT the {"status": ...} envelope most retail routes use and not the
// {"status": "ok"} one the backup routes use -- three envelopes in one API,
// which is why these types spell theirs out instead of reusing CreatedResponse.

/**
 * One registry account.
 *
 * `role` is the value as STORED, which on any install that predates registry
 * v3 is still the legacy 'employee'. `effective_role` is the same row read
 * through `user_accounts.normalize_role()` -- the value every capability
 * decision is actually made against. The UI shows `effective_role` for that
 * reason: a row labelled "employee" that behaves as a cashier everywhere is a
 * label that lies, and the admin choosing whether to promote someone needs
 * the behaviour, not the spelling.
 *
 * There is deliberately no `name`: `users` has no name column (registry_db.py
 * :113-131). A person is identified by their email and the server-assigned
 * `employee_id` (EMP-0001), and inventing a name field the server would drop
 * on the floor would be a form that lies about what it saved.
 */
data class Employee(
    val id: String = "",
    val employee_id: String? = null,
    val email: String? = null,
    val role: String? = null,
    val effective_role: String? = null,
    val clinic_role: String? = null,
    val status: String? = null,
    val created_at: String? = null,
    // Presence only -- the server never sends the PIN hash (see get_employees).
    val has_pin: Boolean = false,
)

data class EmployeesResponse(
    val success: Boolean = false,
    val error: String? = null,
    val employees: List<Employee> = emptyList(),
)

data class CreateEmployeeRequest(val email: String, val role: String)

/**
 * `setup_link` is built server-side as `{request.host_url}/#setup/{token}`.
 * On Android `host_url` is the EMBEDDED server -- http://127.0.0.1:<ephemeral
 * port> -- so the URL as returned is meaningless to anybody but this handset,
 * and worse, the port changes between launches. The screen therefore surfaces
 * the token itself (the segment after `#setup/`), which is the part that is
 * actually the invite, and says where it gets redeemed. See
 * EmployeesScreen.inviteTokenOf().
 */
data class CreateEmployeeResponse(
    val success: Boolean = false,
    val error: String? = null,
    val setup_link: String? = null,
)

data class UpdateEmployeeRoleRequest(val role: String)
data class UpdateEmployeeStatusRequest(val status: String)
data class SetEmployeePinRequest(val pin: String)

/** Shared reply shape for the role / status / PIN mutations. */
data class AdminActionResponse(
    val success: Boolean = false,
    val error: String? = null,
    val role: String? = null,
    val has_pin: Boolean = false,
)
