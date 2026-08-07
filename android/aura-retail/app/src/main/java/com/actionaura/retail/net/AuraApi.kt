package com.actionaura.retail.net

import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.PATCH
import retrofit2.http.POST
import retrofit2.http.PUT
import retrofit2.http.Path
import retrofit2.http.Query

interface AuraApi {
    @GET("api/onboarding/status")
    suspend fun onboardingStatus(): OnboardingStatus

    @POST("api/onboarding/create-admin")
    suspend fun createAdmin(@Body body: CreateAdminRequest): CreateAdminResponse

    @POST("api/auth/login")
    suspend fun login(@Body body: LoginRequest): LoginResponse

    @GET("api/auth/session")
    suspend fun session(): SessionResponse

    @POST("api/auth/logout")
    suspend fun logout()

    @GET("api/sub/clinic/dashboard/stats")
    suspend fun clinicStats(): ClinicStatsResponse

    // Patients
    @GET("api/sub/clinic/patients")
    suspend fun patients(@Query("q") q: String? = null): PatientsResponse

    @POST("api/sub/clinic/patients")
    suspend fun createPatient(@Body body: CreatePatientRequest): CreatedResponse

    @GET("api/sub/clinic/patients/{id}")
    suspend fun patientDetail(@Path("id") id: Int): PatientDetailResponse

    // Appointments
    @GET("api/sub/clinic/appointments")
    suspend fun appointments(@Query("date") date: String? = null, @Query("q") q: String? = null): AppointmentsResponse

    // Prescriptions
    @GET("api/sub/clinic/prescriptions")
    suspend fun prescriptions(@Query("patient_id") patientId: Int? = null): PrescriptionsResponse

    // Invoices
    @GET("api/sub/clinic/invoices")
    suspend fun invoices(): InvoicesResponse

    // Doctors / services
    @GET("api/sub/clinic/doctors")
    suspend fun doctors(): DoctorsResponse

    @GET("api/sub/clinic/services")
    suspend fun services(): ServicesResponse

    // Clinic create flows
    @POST("api/sub/clinic/appointments")
    suspend fun createAppointment(@Body body: CreateAppointmentRequest): CreatedResponse

    @POST("api/sub/clinic/invoices")
    suspend fun createInvoice(@Body body: CreateInvoiceRequest): CreatedResponse

    @POST("api/sub/clinic/payments")
    suspend fun createPayment(@Body body: CreatePaymentRequest): CreatedResponse

    // Lab expenses
    @GET("api/sub/clinic/lab-expenses")
    suspend fun labExpenses(): LabExpensesResponse

    @POST("api/sub/clinic/lab-expenses")
    suspend fun createLabExpense(@Body body: CreateLabExpenseRequest): CreatedResponse

    // ── Retail ───────────────────────────────────────────────────────────────
    @GET("api/sub/retail/dashboard/stats")
    suspend fun retailStats(): RetailStatsResponse

    @GET("api/sub/retail/products")
    suspend fun products(): ProductsResponse

    // Categories (multi-device sync foundation, Task 9 wiring -- see Models.kt)
    @GET("api/sub/retail/categories")
    suspend fun categories(): CategoriesResponse

    @POST("api/sub/retail/categories")
    suspend fun createCategory(@Body body: CreateCategoryRequest): CreatedResponse

    @PUT("api/sub/retail/categories/{id}")
    suspend fun updateCategory(@Path("id") id: String, @Body body: CreateCategoryRequest): CreatedResponse

    @POST("api/sub/retail/products")
    suspend fun createProduct(@Body body: CreateProductRequest): CreatedResponse

    @PATCH("api/sub/retail/products/{id}")
    suspend fun updateProduct(@Path("id") id: Int, @Body body: UpdateProductRequest): CreatedResponse

    @POST("api/sub/retail/products/{id}/stock-adjust")
    suspend fun adjustStock(@Path("id") id: Int, @Body body: AdjustStockRequest): StockAdjustResponse

    @POST("api/sub/retail/sales")
    suspend fun createSale(@Body body: CreateSaleRequest): SaleResponse

    // Customers + credit (AR)
    @GET("api/sub/retail/customers")
    suspend fun customers(@Query("q") q: String? = null): CustomersResponse

    @POST("api/sub/retail/customers")
    suspend fun createCustomer(@Body body: CreateCustomerRequest): CreatedResponse

    @PATCH("api/sub/retail/customers/{id}")
    suspend fun updateCustomerCredit(@Path("id") id: Int, @Body body: UpdateCustomerCreditRequest): CreatedResponse

    @GET("api/sub/retail/customers/receivables")
    suspend fun receivables(): ReceivablesResponse

    @GET("api/sub/retail/customers/{id}/statement")
    suspend fun customerStatement(@Path("id") id: Int): CustomerStatementResponse

    @POST("api/sub/retail/customers/{id}/payments")
    suspend fun customerPayment(@Path("id") id: Int, @Body body: PaymentRequest): PaymentResultResponse

    @POST("api/sub/retail/payments/{id}/void")
    suspend fun voidPayment(@Path("id") id: Int, @Body body: Map<String, String> = mapOf("reason" to "Voided from app")): CreatedResponse

    // Sales history (transactions / invoices)
    @GET("api/sub/retail/sales/recent")
    suspend fun recentSales(@Query("limit") limit: Int = 50): SalesResponse

    @GET("api/sub/retail/sales/{id}")
    suspend fun saleDetail(@Path("id") id: Int): SaleDetailResponse

    // Suppliers
    @GET("api/sub/retail/suppliers")
    suspend fun suppliers(): SuppliersResponse

    @POST("api/sub/retail/suppliers")
    suspend fun createSupplier(@Body body: CreateSupplierRequest): CreatedResponse

    // Purchase orders
    @GET("api/sub/retail/purchase-orders")
    suspend fun purchaseOrders(): PurchaseOrdersResponse

    @GET("api/sub/retail/purchase-orders/{id}")
    suspend fun purchaseOrder(@Path("id") id: Int): PoDetailResponse

    @POST("api/sub/retail/purchase-orders")
    suspend fun createPurchaseOrder(@Body body: CreatePoRequest): CreatedResponse

    @POST("api/sub/retail/purchase-orders/{id}/receive")
    suspend fun receivePurchaseOrder(@Path("id") id: Int, @Body body: Map<String, String> = emptyMap()): CreatedResponse

    @POST("api/sub/retail/purchase-orders/{id}/pay")
    suspend fun payPurchaseOrder(@Path("id") id: Int, @Body body: PaymentRequest): PaymentResultResponse

    // Supplier credit (AP)
    @GET("api/sub/retail/suppliers/payables")
    suspend fun payables(): PayablesResponse

    @GET("api/sub/retail/suppliers/{id}/statement")
    suspend fun supplierStatement(@Path("id") id: Int): SupplierStatementResponse

    @POST("api/sub/retail/suppliers/{id}/payments")
    suspend fun supplierPayment(@Path("id") id: Int, @Body body: PaymentRequest): PaymentResultResponse

    // Daily cash + settings + payment methods
    @GET("api/sub/retail/reports/daily-cash")
    suspend fun dailyCash(@Query("date") date: String? = null): DailyCashResponse

    @GET("api/sub/retail/reports/aging")
    suspend fun aging(@Query("type") type: String): AgingResponse

    @GET("api/sub/retail/settings/credit")
    suspend fun creditSettingsGet(): CreditSettingsResponse

    @POST("api/sub/retail/settings/credit")
    suspend fun creditSettingsSet(@Body body: CreditSettings): CreditSettingsResponse

    @GET("api/sub/retail/payment-methods")
    suspend fun payMethods(): PayMethodsResponse

    @POST("api/sub/retail/payment-methods")
    suspend fun addPayMethod(@Body body: CreatePayMethodRequest): CreatedResponse

    // Returns / refunds
    @GET("api/sub/retail/returns")
    suspend fun returns(): ReturnsResponse

    @POST("api/sub/retail/returns")
    suspend fun createReturn(@Body body: CreateReturnRequest): CreateReturnResponse

    // Reports (period stats)
    @GET("api/sub/retail/reports/summary")
    suspend fun reportSummary(@Query("days") days: Int = 30): ReportSummaryResponse

    @GET("api/sub/retail/reports/payment-methods")
    suspend fun reportPaymentMethods(@Query("days") days: Int = 30): PaymentMethodsResponse

    // Backup / restore (Wave 1A -- admin-only, see commercial_runtime/backup/routes.py)
    @POST("api/backup/create")
    suspend fun createBackup(): CreateBackupResponse

    @GET("api/backup/list")
    suspend fun listBackups(): ListBackupsResponse

    @POST("api/backup/restore")
    suspend fun restoreBackup(@Body body: RestoreBackupRequest): RestoreBackupResponse
}
