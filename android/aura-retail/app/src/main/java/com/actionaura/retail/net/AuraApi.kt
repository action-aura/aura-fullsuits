package com.actionaura.retail.net

import retrofit2.http.Body
import retrofit2.http.DELETE
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

    /**
     * THIS device's own row in the device registry -- and, as a documented and
     * intended side effect of resolving it, the thing that CREATES this
     * install's terminal identity.
     *
     * Not an optional nicety. `retail_api.py::_stamp()` writes
     * `local_terminal_id()` onto every sale, return and stock movement, and
     * that function deliberately PEEKS at `<AURA_APP_DATA>/device/
     * local_device.json` rather than creating it ("stamping a row is a
     * bookkeeping question, not a reason to manufacture an install identity as
     * a side effect"). The only code in the product that creates that file is
     * `device_context.local_device_uuid()`, reachable exclusively from the
     * handlers under `/api/devices` (this one and the two admin-flag POSTs).
     * So until this client calls one of them, every row it writes carries
     * `terminal_id` NULL -- forever, on a device that is itself a till. See
     * net/TerminalIdentity.kt for who calls this and when.
     *
     * (Written as a prefix rather than a glob on purpose: Kotlin block comments
     * NEST, so a literal slash-star inside this KDoc opens a second comment and
     * swallows the rest of the file -- the same trap RetailSession.kt's
     * CAP_REPORTS comment records having already paid one compile for.)
     *
     * `@mt_login_required` + a session `company_id`, so it only answers after
     * login; a 401/403 here is expected before then and must stay harmless.
     */
    @GET("api/devices/me")
    suspend fun myDevice(): MyDeviceResponse

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

    // Launch-readiness "the POS scale fix" -- resolves ONE product by
    // barcode then SKU, company-scoped and index-backed (retail_api.py's
    // lookup_product(), schema v21), instead of fetching the whole
    // products() catalogue just to linear-scan it client-side. Mirrors the
    // desktop/web fix (dfc0ef0/17efa5b) for the Android half of the fleet --
    // see barcode/ProductLookup.kt's lookupProductByCode for the caller.
    // 404 (retrofit2.HttpException) when nothing matches; never a 200 with
    // a null payload, so a genuine "no such product" is distinguishable
    // from every other failure this suspend call can throw.
    @GET("api/sub/retail/products/lookup")
    suspend fun productLookup(@Query("code") code: String): ProductLookupResponse

    // Categories (multi-device sync foundation, Task 9 wiring -- see Models.kt)
    @GET("api/sub/retail/categories")
    suspend fun categories(): CategoriesResponse

    @POST("api/sub/retail/categories")
    suspend fun createCategory(@Body body: CreateCategoryRequest): CreatedIdResponse

    // CreatedIdResponse, not CreatedResponse: CategoriesScreen.kt assigns
    // `val r = if (...) createCategory(...) else updateCategory(...)`, so
    // both branches must share a return type -- see CreatedIdResponse's
    // doc comment in Models.kt for why category/product/customer/supplier
    // create/update responses can't use the Int-keyed CreatedRow.
    @PUT("api/sub/retail/categories/{id}")
    suspend fun updateCategory(@Path("id") id: String, @Body body: CreateCategoryRequest): CreatedIdResponse

    @POST("api/sub/retail/products")
    suspend fun createProduct(@Body body: CreateProductRequest): CreatedIdResponse

    @PATCH("api/sub/retail/products/{id}")
    suspend fun updateProduct(@Path("id") id: String, @Body body: UpdateProductRequest): CreatedResponse

    @POST("api/sub/retail/products/{id}/stock-adjust")
    suspend fun adjustStock(@Path("id") id: String, @Body body: AdjustStockRequest): StockAdjustResponse

    @POST("api/sub/retail/sales")
    suspend fun createSale(@Body body: CreateSaleRequest): SaleResponse

    // Customers + credit (AR)
    @GET("api/sub/retail/customers")
    suspend fun customers(@Query("q") q: String? = null): CustomersResponse

    @POST("api/sub/retail/customers")
    suspend fun createCustomer(@Body body: CreateCustomerRequest): CreatedIdResponse

    @PATCH("api/sub/retail/customers/{id}")
    suspend fun updateCustomerCredit(@Path("id") id: String, @Body body: UpdateCustomerCreditRequest): CreatedResponse

    @GET("api/sub/retail/customers/receivables")
    suspend fun receivables(): ReceivablesResponse

    @GET("api/sub/retail/customers/{id}/statement")
    suspend fun customerStatement(@Path("id") id: String): CustomerStatementResponse

    @POST("api/sub/retail/customers/{id}/payments")
    suspend fun customerPayment(@Path("id") id: String, @Body body: PaymentRequest): PaymentResultResponse

    @POST("api/sub/retail/payments/{id}/void")
    suspend fun voidPayment(@Path("id") id: Int, @Body body: Map<String, String> = mapOf("reason" to "Voided from app")): CreatedResponse

    // Sales history (transactions / invoices)
    @GET("api/sub/retail/sales/recent")
    suspend fun recentSales(@Query("limit") limit: Int = 50): SalesResponse

    @GET("api/sub/retail/sales/{id}")
    suspend fun saleDetail(@Path("id") id: Int): SaleDetailResponse

    // Real-sale receipt bytes for network/LAN ESC/POS printing (retail-
    // hardware-viewports). sale_id is Int, matching SaleResult.id/
    // saleDetail's @Path above -- retail_api.py's printer_receipt_payload
    // reads it with request.args.get('sale_id', type=int). kick is sent as
    // 0/1 (not a Boolean) because it travels as a query string, same as the
    // route's own `?kick=1` documented shape.
    @GET("api/sub/retail/printer/receipt-payload")
    suspend fun receiptPayload(
        @Query("sale_id") saleId: Int,
        @Query("width") width: Int,
        @Query("kick") kick: Int,
    ): ReceiptPayloadResponse

    // Suppliers
    @GET("api/sub/retail/suppliers")
    suspend fun suppliers(): SuppliersResponse

    @POST("api/sub/retail/suppliers")
    suspend fun createSupplier(@Body body: CreateSupplierRequest): CreatedIdResponse

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
    suspend fun supplierStatement(@Path("id") id: String): SupplierStatementResponse

    @POST("api/sub/retail/suppliers/{id}/payments")
    suspend fun supplierPayment(@Path("id") id: String, @Body body: PaymentRequest): PaymentResultResponse

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

    // This device's branch pin (Wave C1 -- see Models.kt's Branch/DeviceBranch
    // doc comments for the full "why"). GET requires only CAP_SELL (a cashier
    // needs to see whether this till is unpinned, the state that silently
    // produces wrong data on a chain); POST requires CAP_EMPLOYEES (pinning a
    // till is an administrative act) -- see RetailSettingsScreen in
    // ui/screens/RetailExtraScreens.kt for how a 403 on the POST is handled
    // as a designed state, not a failure. (It lived in ui/screens/
    // SettingsScreen.kt originally, a file that was never wired into any nav
    // route and so never rendered for a single user -- moved here and that
    // file deleted for exactly that reason.)
    @GET("api/sub/retail/device/branch")
    suspend fun deviceBranch(): DeviceBranchResponse

    @POST("api/sub/retail/device/branch")
    suspend fun setDeviceBranch(@Body body: SetDeviceBranchRequest): DeviceBranchResponse

    // The company's branches, to populate the picker above. No capability
    // gate server-side beyond being signed in to this subsystem -- any
    // signed-in retail user may see the list, same as list_active_promotions.
    @GET("api/sub/retail/branches")
    suspend fun branches(): BranchesResponse

    // Read once at boot, for how to format money and nothing else. Same gating
    // as branches() above: any signed-in retail user may read it, because a
    // cashier's till cannot render a single price without it.
    @GET("api/sub/retail/settings/tax")
    suspend fun taxSettings(): TaxSettingsResponse

    // Returns / refunds
    @GET("api/sub/retail/returns")
    suspend fun returns(): ReturnsResponse

    @POST("api/sub/retail/returns")
    suspend fun createReturn(@Body body: CreateReturnRequest): CreateReturnResponse

    // Aura AI assistant (retail_api.py::ai_chat, blueprint prefix
    // /api/sub/retail). Non-streaming contract only -- see AiChatRequest's
    // doc comment in Models.kt for why `stream` is never sent. This call
    // legitimately takes ~17-36s (CPU-bound generation on the hosted
    // droplet), which is why ApiClient raises the read timeout for exactly
    // this path.
    @POST("api/sub/retail/ai/chat")
    suspend fun aiChat(@Body body: AiChatRequest): AiChatResponse

    // Reports (period stats)
    @GET("api/sub/retail/reports/summary")
    suspend fun reportSummary(@Query("days") days: Int = 30): ReportSummaryResponse

    @GET("api/sub/retail/reports/payment-methods")
    suspend fun reportPaymentMethods(@Query("days") days: Int = 30): PaymentMethodsResponse

    // Takings and transaction count per employee, from `sales.actor_user_uid`
    // (retail schema v13). Same gate as its siblings above -- server-side
    // @mt_require_capability('retail.reports') -- and the same
    // {"success": true, "data": ...} envelope the reports family uses rather
    // than the {"status": ...} one most of this API answers with.
    //
    // An embedded server that predates this route answers 404. That is a real,
    // expected state on a handset whose APK has been updated ahead of its
    // bundled backend, and EmployeeSalesScreen says so in words instead of
    // letting it read as a generic server fault.
    @GET("api/sub/retail/reports/by-employee")
    suspend fun reportByEmployee(@Query("days") days: Int = 30): ByEmployeeResponse

    // Backup / restore (Wave 1A -- admin-only, see commercial_runtime/backup/routes.py)
    @POST("api/backup/create")
    suspend fun createBackup(): CreateBackupResponse

    @GET("api/backup/list")
    suspend fun listBackups(): ListBackupsResponse

    @POST("api/backup/restore")
    suspend fun restoreBackup(@Body body: RestoreBackupRequest): RestoreBackupResponse

    // ── Employees (Phase 1 -- admin-only, see
    //    commercial_runtime/identity/onboarding_routes.py) ────────────────────
    // Shared with Clinic and with the desktop shell: the SAME blueprint the
    // desktop talks to, reached here over the embedded 127.0.0.1 server. Every
    // one of these re-checks `session['mt_role'] == 'admin'` server-side, so
    // RetailSession.isAdmin gating in the UI is convenience, never the control.
    @GET("api/admin/employees")
    suspend fun employees(): EmployeesResponse

    @POST("api/admin/employees")
    suspend fun createEmployee(@Body body: CreateEmployeeRequest): CreateEmployeeResponse

    @PUT("api/admin/employees/{id}/role")
    suspend fun updateEmployeeRole(@Path("id") id: String, @Body body: UpdateEmployeeRoleRequest): AdminActionResponse

    @PUT("api/admin/employees/{id}/status")
    suspend fun updateEmployeeStatus(@Path("id") id: String, @Body body: UpdateEmployeeStatusRequest): AdminActionResponse

    @PUT("api/admin/employees/{id}/pin")
    suspend fun setEmployeePin(@Path("id") id: String, @Body body: SetEmployeePinRequest): AdminActionResponse

    // Retrofit refuses a @DELETE with a @Body by default, and the route needs
    // none -- clearing is expressed by the method, not by a payload.
    @DELETE("api/admin/employees/{id}/pin")
    suspend fun clearEmployeePin(@Path("id") id: String): AdminActionResponse
}
