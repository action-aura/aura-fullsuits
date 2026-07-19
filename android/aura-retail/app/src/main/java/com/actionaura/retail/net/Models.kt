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
data class Product(
    val id: Int = 0, val sku: String? = null, val name: String? = null,
    val sell_price: Double = 0.0, val cost_price: Double = 0.0, val tax_rate: Double = 0.0,
    val total_stock: Double = 0.0, val reorder_level: Int = 0, val unit: String? = null,
    val category_name: String? = null,
    val barcode: String? = null,   // returned by the Flask API; used for camera-scan lookup
)
data class ProductsResponse(val status: String = "", val data: List<Product> = emptyList())

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
data class SaleItemReq(val product_id: Int, val quantity: Double, val discount_pct: Double = 0.0)

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
    val customer_id: Int? = null, val due_date: String? = null,
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
data class Customer(
    val id: Int = 0, val name: String? = null, val phone: String? = null, val email: String? = null,
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
)
data class SalesResponse(val status: String = "", val data: List<Sale> = emptyList())

data class SaleLine(
    val product_id: Int = 0,
    val product_name: String? = null, val sku: String? = null,
    val quantity: Double = 0.0, val unit_price: Double = 0.0, val line_total: Double = 0.0,
)
data class SaleDetail(val sale: Sale? = null, val items: List<SaleLine> = emptyList())
data class SaleDetailResponse(val status: String = "", val data: SaleDetail? = null)

// ── Retail: suppliers ────────────────────────────────────────────────────────
data class Supplier(
    val id: Int = 0, val name: String? = null, val phone: String? = null,
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

data class PoItemReq(val product_id: Int, val quantity: Double, val unit_cost: Double)
data class CreatePoRequest(val supplier_id: Int, val notes: String = "", val items: List<PoItemReq>,
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
data class ReturnItemReq(val product_id: Int, val quantity: Double)
data class CreateReturnRequest(
    val sale_id: Int, val reason: String = "Customer return",
    val refund_method: String = "cash", val items: List<ReturnItemReq>,
    // Required for real duplicate-submission protection (a double-tap on
    // "Process refund" must not create two returns) -- the source app
    // never generated one at all, so no return request was ever
    // deduplicated client-side prior to this phase.
    val idempotency_key: String,
)

// Server-computed, tax-inclusive refund breakdown -- mirrors
// docs/architecture/financial-authority-contracts.md's Retail return
// contract. The client must display refund_amount from here, never a
// locally-summed estimate, once the return has actually been submitted.
data class ReturnItemResult(
    val product_id: Int = 0, val quantity: Double = 0.0, val unit_price: Double = 0.0,
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
