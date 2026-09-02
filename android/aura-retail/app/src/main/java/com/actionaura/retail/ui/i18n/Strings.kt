package com.actionaura.retail.ui.i18n

import android.content.Context
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue

/**
 * Lightweight in-app localization for the retail (Aura POS) UI.
 *
 * Why not Android resources (strings.xml + values-ar)?  The app is 100% Jetpack
 * Compose on a bare [androidx.activity.ComponentActivity] (no AppCompat), and most
 * user-facing text is built with Kotlin string interpolation. A central map keyed by
 * the *English* string lets us wrap call sites with [tr] without inventing hundreds of
 * resource IDs, and — crucially — anything not yet translated falls back to readable
 * English instead of a blank or a crash.
 *
 * The chosen language is held in observable Compose state ([AppLocale.lang]) and
 * persisted in SharedPreferences, so switching is instant (recomposition) — no Activity
 * restart. RTL is handled by overriding LocalLayoutDirection at the app root for Arabic.
 */
enum class AppLang(val tag: String, val nativeName: String) {
    EN("en", "English"),
    AR("ar", "العربية"),
}

object AppLocale {
    private const val PREFS = "aura_prefs"
    private const val KEY = "app_lang"

    /** Active language. Reading this inside a composable makes it recompose on change. */
    var lang by mutableStateOf(AppLang.EN)
        private set

    val isRtl: Boolean get() = lang == AppLang.AR

    fun load(ctx: Context) {
        val tag = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, AppLang.EN.tag)
        lang = AppLang.entries.firstOrNull { it.tag == tag } ?: AppLang.EN
    }

    fun set(ctx: Context, value: AppLang) {
        lang = value
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY, value.tag).apply()
    }
}

/**
 * Translate an English UI string to the active language. Falls back to the English
 * key when no translation exists, so partial coverage degrades gracefully.
 *
 * Usage: `Text(tr("Search products"))`. For interpolated text, translate a template
 * with format placeholders, e.g. `tr("Only %s in stock").format(fmtQty(n))`.
 */
fun tr(en: String): String =
    if (AppLocale.lang == AppLang.AR) AR_STRINGS[en] ?: en else en

private val AR_STRINGS: Map<String, String> = mapOf(
    // ── Navigation / app shell ────────────────────────────────────────────────
    "Dashboard" to "لوحة التحكم",
    "POS" to "نقطة البيع",
    "Products" to "المنتجات",
    "More" to "المزيد",
    "Settings" to "الإعدادات",
    "Log out" to "تسجيل الخروج",
    // Session section in MoreScreen -- added when the two-item navigation
    // drawer was deleted and Log out moved into the overflow list.
    "Session" to "الجلسة",
    "End this session on this device" to "إنهاء الجلسة على هذا الجهاز",
    "Back" to "رجوع",
    "Menu" to "القائمة",
    // The startup-failure screen. The single worst place in the app to fall
    // back to English: it is what an Arabic-speaking owner sees when nothing
    // else in the app will load, and it is the screen that has to convince
    // them their data is intact.
    "Aura could not start" to "تعذّر تشغيل Aura",
    "The app's built-in server did not start, so nothing can be loaded or saved. " +
        "Your data is untouched. Please try again; if this keeps happening, restart the " +
        "device and send the details below to support." to
        "لم يبدأ الخادم المدمج في التطبيق، لذا لا يمكن تحميل أي بيانات أو حفظها. بياناتك سليمة ولم تتأثر. " +
        "يرجى المحاولة مرة أخرى؛ وإذا تكرر ذلك، أعد تشغيل الجهاز وأرسل التفاصيل أدناه إلى الدعم.",
    "Reports" to "التقارير",
    "Transactions" to "المعاملات",
    "Returns" to "المرتجعات",
    "Customers" to "العملاء",
    "Receivables" to "الذمم المدينة",
    "Suppliers" to "الموردون",
    "Purchase Orders" to "أوامر الشراء",
    "Payables" to "الذمم الدائنة",
    "Cash Summary" to "ملخص النقدية",
    "Aging" to "أعمار الديون",
    "Patient" to "المريض",
    "Action Aura" to "Action Aura",
    "Starting…" to "جارٍ البدء…",

    // ── AI sheet ──────────────────────────────────────────────────────────────
    "Ask about your business" to "اسأل عن نشاطك التجاري",
    "SUGGESTED" to "مقترحات",
    "Ask anything…" to "اسأل أي شيء…",
    "Send" to "إرسال",
    "Aura AI is thinking…" to "Aura AI يفكّر…",
    "AI assistant is temporarily unavailable." to "مساعد الذكاء الاصطناعي غير متاح مؤقتًا.",
    "Today's best sellers" to "الأكثر مبيعًا اليوم",
    "Low stock items" to "أصناف المخزون المنخفض",
    "Sales vs last week" to "المبيعات مقابل الأسبوع الماضي",
    "Slow-moving products" to "المنتجات بطيئة الحركة",

    // ── Dashboard ─────────────────────────────────────────────────────────────
    "Good morning" to "صباح الخير",
    "Good afternoon" to "نهارك سعيد",
    "Good evening" to "مساء الخير",
    "Your store at a glance" to "متجرك في لمحة",
    "Quick actions" to "إجراءات سريعة",
    "New Sale" to "بيع جديد",
    "Share Receipt" to "مشاركة الإيصال",
    "Today" to "اليوم",
    "Today's Sales" to "مبيعات اليوم",
    "Revenue today" to "إيرادات اليوم",
    "Catalog" to "الكتالوج",
    "Low Stock" to "مخزون منخفض",
    "Need reorder" to "يحتاج إعادة طلب",

    // ── POS ───────────────────────────────────────────────────────────────────
    "Search products" to "ابحث عن المنتجات",
    "Scan barcode" to "مسح الباركود",
    "Held" to "معلّقة",
    "No products yet" to "لا توجد منتجات بعد",
    "No matches" to "لا توجد نتائج",
    "Add products to start selling." to "أضف منتجات لبدء البيع.",
    "Try another search or category." to "جرّب بحثًا أو فئة أخرى.",
    "Only %s in stock" to "%s فقط في المخزون",
    "Max stock: %s" to "الحد الأقصى للمخزون: %s",
    "View cart" to "عرض السلة",
    "Current Sale" to "البيع الحالي",
    "Walk-in customer" to "عميل عابر",
    "Walk-in (no customer)" to "عميل عابر (بدون عميل)",
    "Outstanding" to "المستحق",
    "limit" to "الحد",
    "unlimited credit" to "ائتمان غير محدود",
    "no credit" to "بدون ائتمان",
    "Total" to "الإجمالي",
    "Payment method" to "طريقة الدفع",
    "Cash" to "نقدًا",
    "Card" to "بطاقة",
    "Transfer" to "تحويل",
    "Credit" to "آجل",
    "Paid now (optional) — rest goes on credit" to "المدفوع الآن (اختياري) — الباقي آجل",
    "Hold sale (park for later)" to "تعليق البيع (للمتابعة لاحقًا)",
    "Select a customer for credit sales (walk-in not allowed)" to "اختر عميلاً للمبيعات الآجلة (العميل العابر غير مسموح)",
    "Sale failed" to "فشل البيع",
    "Couldn't reach the server" to "تعذّر الوصول إلى الخادم",
    "Charge" to "تحصيل",
    "Held sales" to "المبيعات المعلّقة",
    "Resuming parks the current cart first, so nothing is lost." to "استئناف بيع يعلّق السلة الحالية أولاً، فلا يضيع شيء.",
    "No held sales." to "لا توجد مبيعات معلّقة.",
    "%d item(s)" to "%d صنف",
    "Discard" to "تجاهل",
    "Resume" to "استئناف",
    "Not found:" to "غير موجود:",
    "in stock" to "في المخزون",
    "Payment successful" to "تمت عملية الدفع بنجاح",
    "collected" to "تم تحصيله",

    // ── Shared API error mapping (net/ApiErrors.kt) ──────────────────────────
    // "This action is not available..." is flask_guard.py's fixed 403 message
    // text, translated verbatim so the licensing block reads natively in
    // Arabic instead of falling back to the English server string.
    "Blocked by your subscription/license:" to "محظور بسبب اشتراكك/ترخيصك:",
    "This action is not available in the current licensing state." to "هذا الإجراء غير متاح في حالة الترخيص الحالية.",
    "Your session has expired. Please log in again." to "انتهت صلاحية جلستك. يرجى تسجيل الدخول مرة أخرى.",
    "Server error" to "خطأ في الخادم",
    "Unexpected error" to "خطأ غير متوقع",

    // ── Licensing: the awaiting-approval screen (ui/screens/LicensingScreen.kt)
    // The three lines that make a factual claim about what the app has just
    // done -- "we are checking", "we last checked at X", "we have stopped
    // checking". They are the ones a customer reads back to support over the
    // phone, so an English fallback here is worse than anywhere else on the
    // screen. (The rest of the licensing surface is still untranslated; see
    // the note in LicensingScreen.kt.)
    "Checking with the licensing service automatically every %s seconds…"
        to "تتم المراجعة تلقائيًا مع خدمة التراخيص كل %s ثانية…",
    "Still waiting for approval. Last checked at %s."
        to "ما زال طلبك بانتظار الموافقة. آخر مراجعة في %s.",
    "Could not reach the licensing service. Your key is still held for approval — " +
        "press Check Now to try again when you are back online."
        to "تعذّر الوصول إلى خدمة التراخيص. ما زال مفتاحك محفوظًا بانتظار الموافقة — " +
            "اضغط \"تحقّق الآن\" للمحاولة مرة أخرى عند عودة الاتصال.",
    "Check Now" to "تحقّق الآن",
    "Use a different key" to "استخدام مفتاح آخر",

    // ── Products ──────────────────────────────────────────────────────────────
    "Add your first product to start selling." to "أضف أول منتج لك لبدء البيع.",
    "Add Product" to "إضافة منتج",
    "Product added" to "تمت إضافة المنتج",
    "Edit Product" to "تعديل المنتج",
    "Product name *" to "اسم المنتج *",
    "Sell price" to "سعر البيع",
    "Cost price" to "سعر التكلفة",
    "Tax %" to "الضريبة %",
    "Reorder level" to "حد إعادة الطلب",
    "Barcode" to "الباركود",
    "Adjust stock" to "تعديل المخزون",
    "Enter a positive number to add stock, negative to remove." to "أدخل رقمًا موجبًا لإضافة مخزون، وسالبًا للإزالة.",
    "e.g. +50 or -3" to "مثال: +50 أو -3",
    "Product name is required" to "اسم المنتج مطلوب",
    "Product updated" to "تم تحديث المنتج",
    "Couldn't save" to "تعذّر الحفظ",
    "Save Changes" to "حفظ التغييرات",
    "Unit:" to "الوحدة:",
    "SKU / barcode *" to "رمز SKU / الباركود *",
    "Initial stock" to "المخزون الأولي",
    "Name and SKU are required" to "الاسم ورمز SKU مطلوبان",
    "Save Product" to "حفظ المنتج",
    "Out" to "نفد",
    "Low" to "منخفض",
    "%s in stock" to "%s في المخزون",

    // ── Login / Setup ─────────────────────────────────────────────────────────
    "Enter email and password" to "أدخل البريد الإلكتروني وكلمة المرور",
    "Invalid credentials" to "بيانات الدخول غير صحيحة",
    "Couldn't reach the server. Try again." to "تعذّر الوصول إلى الخادم. حاول مرة أخرى.",
    "Sign in to your workspace" to "سجّل الدخول إلى مساحة عملك",
    "Email" to "البريد الإلكتروني",
    "Password" to "كلمة المرور",
    "Sign In" to "تسجيل الدخول",
    "Email and a 6+ char password required" to "البريد الإلكتروني وكلمة مرور من 6 أحرف على الأقل مطلوبة",
    "Couldn't create account" to "تعذّر إنشاء الحساب",
    "Welcome to Action Aura" to "مرحبًا بك في Action Aura",
    "Create your administrator account" to "أنشئ حساب المسؤول",
    "Your name" to "اسمك",
    "Business / store name" to "اسم النشاط / المتجر",
    "Create Account" to "إنشاء حساب",

    // ── Barcode scanner ───────────────────────────────────────────────────────
    "Keep scanning — tap Done when finished" to "واصل المسح — اضغط تم عند الانتهاء",
    "Done" to "تم",
    "Point the camera at a barcode" to "وجّه الكاميرا نحو الباركود",
    "Close scanner" to "إغلاق الماسح",
    "Camera permission denied" to "تم رفض إذن الكاميرا",
    "Camera unavailable" to "الكاميرا غير متاحة",
    "You can still enter the barcode manually below." to "يمكنك إدخال الباركود يدويًا أدناه.",
    "Use this barcode" to "استخدم هذا الباركود",

    // ── More hub ──────────────────────────────────────────────────────────────
    "Records" to "السجلات",
    // "Categories" and its subtitle were shipped on the More hub with no entry
    // here, so the very first row of the Records section rendered English on
    // an Arabic till. Exactly the silent tr() fallback the per-screen coverage
    // tests exist to surface; found by adding RetailExtraScreens.kt to that
    // scan, not by anybody noticing.
    "Categories" to "الفئات",
    "Group products for filtering" to "تجميع المنتجات لتسهيل التصفية",
    "Sales stats by time period" to "إحصاءات المبيعات حسب الفترة",
    "Past sales & invoices" to "المبيعات والفواتير السابقة",
    "Refund items from a sale" to "استرجاع أصناف من عملية بيع",
    "Customers & credit" to "العملاء والائتمان",
    "Manage customers & credit" to "إدارة العملاء والائتمان",
    "Who owes you & repayments" to "من يدين لك والسداد",
    "Purchasing" to "المشتريات",
    "Vendors you buy from" to "الموردون الذين تشتري منهم",
    "Restock & receive stock" to "إعادة التخزين واستلام البضائع",
    "What you owe suppliers" to "ما تدين به للموردين",
    "Finance" to "المالية",
    "Daily cash in / out / net" to "النقد اليومي الداخل / الخارج / الصافي",
    "Receivables & payables by age" to "الذمم المدينة والدائنة حسب العمر",
    "Credit, currency & payment methods" to "الائتمان والعملة وطرق الدفع",
    // Shipped untranslated on both the Settings hub and the licensing screen's
    // own top bar. Same silent-fallback class as "Categories" above.
    "Licensing" to "الترخيص",

    // ── Reports ───────────────────────────────────────────────────────────────
    "7 days" to "7 أيام",
    "30 days" to "30 يومًا",
    "90 days" to "90 يومًا",
    "1 year" to "سنة واحدة",
    "vs previous period" to "مقارنة بالفترة السابقة",
    "Revenue" to "الإيرادات",
    "sales" to "مبيعات",
    "Gross Profit" to "إجمالي الربح",
    "margin" to "هامش",
    "Avg Ticket" to "متوسط الفاتورة",
    "per sale" to "لكل عملية بيع",
    "Inventory Value (at cost)" to "قيمة المخزون (بالتكلفة)",
    "current stock on hand" to "المخزون الحالي المتوفر",
    "Payment methods" to "طرق الدفع",

    // ── Reports → By employee (retail schema v13 attribution) ─────────────────
    // "غير منسوبة" ("not attributed") rather than "غير معروف" ("unknown"):
    // the shop DOES know these sales happened and what they were worth; what
    // is missing is only the link to a person. Translating it as "unknown"
    // would suggest the takings themselves are in doubt, which is a stronger
    // and false claim.
    "By Employee" to "حسب الموظف",
    "Takings and transactions per employee" to "الإيرادات وعدد العمليات لكل موظف",
    "Reports access required" to "مطلوب صلاحية التقارير",
    "Only accounts with reports access can see takings by employee. " +
        "Ask the owner to grant it." to
        "يمكن فقط للحسابات التي تملك صلاحية التقارير الاطّلاع على الإيرادات حسب الموظف. اطلب من المالك منحك هذه الصلاحية.",
    "Couldn't load takings by employee" to "تعذّر تحميل الإيرادات حسب الموظف",
    "No sales in this period" to "لا توجد مبيعات في هذه الفترة",
    "Nothing was rung up in the selected period. " +
        "Choose a longer period to see more." to
        "لم تُسجَّل أي عملية بيع في الفترة المحددة. اختر فترة أطول لعرض المزيد.",
    "Not attributed" to "غير منسوبة",
    "Sales rung before this app recorded who served them." to
        "مبيعات نُفِّذت قبل أن يبدأ التطبيق بتسجيل من قام بها.",
    "Account removed" to "حساب محذوف",
    "The account that rang these sales no longer exists." to
        "الحساب الذي نفّذ هذه المبيعات لم يعد موجودًا.",
    "Every sale in the period is counted here, including any the " +
        "app could not attribute." to
        "تُحتسب هنا كل عمليات البيع في الفترة، بما فيها ما تعذّر على التطبيق نسبته إلى موظف.",
    "This install's server doesn't provide takings by employee yet." to
        "لا يوفّر خادم هذا التثبيت الإيرادات حسب الموظف بعد.",
    // The last-resort wording for a 200 whose envelope refuses without naming a
    // reason. The server's own sentence is preferred whenever it sends one --
    // see byEmployeeRefusalOrNull -- so this only shows when there is genuinely
    // nothing to quote, and it must still not read as "the report is empty".
    "The server wouldn't send this report." to
        "رفض الخادم إرسال هذا التقرير.",
    "Rung by" to "نفّذها",
    "Rung by an account that no longer exists" to "نفّذها حساب لم يعد موجودًا",
    "Who rang this sale was not recorded — it predates employee attribution." to
        "لم يُسجَّل من نفّذ عملية البيع هذه — فهي أقدم من ميزة نسب المبيعات إلى الموظفين.",

    // ── Transactions / receipt ────────────────────────────────────────────────
    "No transactions yet" to "لا توجد معاملات بعد",
    "Sales you complete in POS will show up here as invoices." to "ستظهر المبيعات التي تتمّها في نقطة البيع هنا كفواتير.",
    "Walk-in" to "عميل عابر",
    "Receipt" to "إيصال",
    "Couldn't load this transaction." to "تعذّر تحميل هذه المعاملة.",
    "Item" to "صنف",
    "Subtotal" to "المجموع الفرعي",
    "Discount" to "الخصم",
    "Tax" to "الضريبة",
    "Paid" to "المدفوع",
    "Change" to "الباقي",

    // ── Returns ───────────────────────────────────────────────────────────────
    "No returns yet" to "لا توجد مرتجعات بعد",
    "Refund items from a past sale; stock is added back automatically." to "استرجع أصنافًا من عملية بيع سابقة؛ تتم إعادة المخزون تلقائيًا.",
    "Process return" to "تنفيذ مرتجع",
    "Return processed" to "تم تنفيذ المرتجع",
    "Process Return" to "تنفيذ مرتجع",
    "Select a receipt" to "اختر إيصالاً",
    "No sales found" to "لا توجد مبيعات",
    "Select items to return" to "اختر الأصناف المراد إرجاعها",
    "Choose quantity to return" to "اختر الكمية المراد إرجاعها",
    "Choose a quantity to return" to "اختر كمية للإرجاع",
    "Sold" to "المُباع",
    "Refund total" to "إجمالي الاسترداد",
    "Reason" to "السبب",
    "Customer return" to "إرجاع العميل",
    "Defective / damaged" to "معيب / تالف",
    "Wrong item" to "صنف خاطئ",
    "Not as described" to "ليس كما هو موصوف",
    "Changed mind" to "تغيير الرأي",
    "Refund method" to "طريقة الاسترداد",
    "Select at least one item" to "اختر صنفًا واحدًا على الأقل",
    "Couldn't process" to "تعذّرت المعالجة",
    "Process refund" to "تنفيذ الاسترداد",

    // ── Customers ─────────────────────────────────────────────────────────────
    "Unlimited credit" to "ائتمان غير محدود",
    "Limited credit" to "ائتمان محدود",
    "No credit" to "بدون ائتمان",
    "No customers yet" to "لا يوجد عملاء بعد",
    "Add customers to track loyalty and credit." to "أضف عملاء لتتبّع الولاء والائتمان.",
    "Add customer" to "إضافة عميل",
    "Customer added" to "تمت إضافة العميل",
    "Credit settings saved" to "تم حفظ إعدادات الائتمان",
    "owes" to "مدين بـ",
    "Add Customer" to "إضافة عميل",
    "Full name *" to "الاسم الكامل *",
    "Phone" to "الهاتف",
    "Name is required" to "الاسم مطلوب",
    "Save Customer" to "حفظ العميل",
    "Customer" to "عميل",
    "Credit mode" to "وضع الائتمان",
    "Limited" to "محدود",
    "Unlimited" to "غير محدود",
    "Credit limit" to "حد الائتمان",
    "Save credit settings" to "حفظ إعدادات الائتمان",

    // ── Receivables / statements ──────────────────────────────────────────────
    "Nothing outstanding" to "لا توجد مستحقات",
    "Credit sales that aren't fully paid will appear here." to "ستظهر هنا المبيعات الآجلة غير المسددة بالكامل.",
    "TOTAL RECEIVABLE" to "إجمالي الذمم المدينة",
    "Payment recorded" to "تم تسجيل الدفعة",
    "Statement" to "كشف حساب",
    "Balance" to "الرصيد",
    "Couldn't load statement." to "تعذّر تحميل كشف الحساب.",
    "Credit sale" to "بيع آجل",
    "Payment" to "دفعة",
    "Void" to "إلغاء",
    "Record a payment" to "تسجيل دفعة",
    "Amount" to "المبلغ",
    "Enter a valid amount" to "أدخل مبلغًا صحيحًا",
    "Failed" to "فشل",
    "Pay" to "دفع",

    // ── Suppliers ─────────────────────────────────────────────────────────────
    "No suppliers yet" to "لا يوجد موردون بعد",
    "Add the vendors you buy stock from." to "أضف الموردين الذين تشتري منهم البضائع.",
    "Add Supplier" to "إضافة مورد",
    "Supplier added" to "تمت إضافة المورد",
    "PO" to "أمر شراء",
    "Company name *" to "اسم الشركة *",
    "Address" to "العنوان",
    "Company name is required" to "اسم الشركة مطلوب",
    "Save Supplier" to "حفظ المورد",

    // ── Payables ──────────────────────────────────────────────────────────────
    "Nothing owed" to "لا توجد التزامات",
    "Unpaid purchase orders will appear here." to "ستظهر هنا أوامر الشراء غير المسددة.",
    "TOTAL PAYABLE" to "إجمالي الذمم الدائنة",
    "You owe" to "أنت مدين بـ",

    // ── Daily cash ────────────────────────────────────────────────────────────
    "‹ Prev" to "‹ السابق",
    "Next ›" to "التالي ›",
    "Cash In" to "النقد الداخل",
    "received" to "مستلم",
    "Cash Out" to "النقد الخارج",
    "paid out" to "مدفوع",
    "Net Cash" to "صافي النقد",
    "in − out" to "داخل − خارج",
    "By method" to "حسب الطريقة",
    "IN" to "داخل",
    "OUT" to "خارج",

    // ── Aging ─────────────────────────────────────────────────────────────────
    "Receivable" to "ذمم مدينة",
    "Payable" to "ذمم دائنة",
    "Current" to "حالي",
    "1–30 days" to "1–30 يومًا",
    "31–60 days" to "31–60 يومًا",
    "61–90 days" to "61–90 يومًا",
    "90+ days" to "90+ يومًا",
    "Each bucket holds the outstanding balance of parties whose oldest unpaid document falls in that age range." to "تحتوي كل فئة على الرصيد المستحق للأطراف الذين يقع أقدم مستند غير مسدد لهم في هذا النطاق العمري.",

    // ── Retail settings ───────────────────────────────────────────────────────
    "Credit policy" to "سياسة الائتمان",
    "Default credit mode for new customers" to "وضع الائتمان الافتراضي للعملاء الجدد",
    "Default credit limit" to "حد الائتمان الافتراضي",
    "When a credit limit is exceeded" to "عند تجاوز حد الائتمان",
    "Warn (allow the sale)" to "تحذير (السماح بالبيع)",
    "Block the sale" to "منع البيع",
    "Warn" to "تحذير",
    "Base currency (e.g. USD)" to "العملة الأساسية (مثال: USD)",
    "Settings saved" to "تم حفظ الإعدادات",
    "Save settings" to "حفظ الإعدادات",
    "New method" to "طريقة جديدة",
    "Couldn't add" to "تعذّرت الإضافة",
    "Add" to "إضافة",

    // ── Purchase orders ───────────────────────────────────────────────────────
    "No purchase orders" to "لا توجد أوامر شراء",
    "Create a PO to restock from a supplier." to "أنشئ أمر شراء لإعادة التخزين من مورد.",
    "New Purchase Order" to "أمر شراء جديد",
    "Purchase order created" to "تم إنشاء أمر الشراء",
    "Stock received" to "تم استلام البضائع",
    "New PO" to "أمر شراء جديد",
    "Purchase order" to "أمر شراء",
    "Couldn't load this purchase order." to "تعذّر تحميل أمر الشراء هذا.",
    "Payment:" to "الدفع:",
    "Pay supplier" to "دفع للمورد",
    "Receive stock" to "استلام البضائع",
    "Items" to "الأصناف",
    "Select supplier" to "اختر موردًا",
    "No suppliers — add one first" to "لا يوجد موردون — أضف موردًا أولاً",
    "Select product" to "اختر منتجًا",
    "Qty" to "الكمية",
    "Unit cost" to "تكلفة الوحدة",
    "Add item" to "إضافة صنف",
    "Remove" to "إزالة",
    "Order total" to "إجمالي الأمر",
    "Amount paid now (blank = on credit)" to "المبلغ المدفوع الآن (فارغ = آجل)",
    "Select a supplier" to "اختر موردًا",
    "Add at least one item" to "أضف صنفًا واحدًا على الأقل",
    "Create Purchase Order" to "إنشاء أمر شراء",

    // ── Settings (main) ───────────────────────────────────────────────────────
    "Appearance" to "المظهر",
    "Theme" to "السمة",
    "System default" to "افتراضي النظام",
    "Language" to "اللغة",
    "Notifications" to "الإشعارات",
    "Reminders & alerts" to "التذكيرات والتنبيهات",
    "Security" to "الأمان",
    "Biometric lock" to "القفل البيومتري",
    "Unlock with fingerprint" to "إلغاء القفل ببصمة الإصبع",
    "Change password" to "تغيير كلمة المرور",
    "Backup & restore" to "النسخ الاحتياطي والاستعادة",
    "About" to "حول",
    "Version" to "الإصدار",
    "Privacy policy" to "سياسة الخصوصية",
    "Terms of service" to "شروط الخدمة",
    " — coming soon" to " — قريبًا",
    "Choose language" to "اختر اللغة",

    // ── Settings → this device's branch (Wave C1) ────────────────────────────
    // The Android half of the dc22b04 fix -- see net/Models.kt's Branch/
    // DeviceBranch doc comments and RetailSettingsScreen in
    // ui/screens/RetailExtraScreens.kt for the full "why". "Loading…" is used
    // generically enough to belong here rather than scoped to one screen.
    "Loading…" to "جارٍ التحميل…",
    "This Device's Branch" to "فرع هذا الجهاز",
    "Couldn't load this device's branch" to "تعذّر تحميل فرع هذا الجهاز",
    "No branch pinned" to "لا يوجد فرع مثبَّت",
    "Sales on this till file under the company's default branch. Worth checking on a multi-branch chain." to
        "تُسجَّل مبيعات هذا الصندوق تحت الفرع الافتراضي للشركة. يستحق التحقق في سلسلة متعددة الفروع.",
    "Sales rung on this till are filed under this branch." to
        "تُسجَّل المبيعات التي تتم على هذا الصندوق تحت هذا الفرع.",
    "Only the owner can change which branch this device is pinned to. Ask the owner to make the change on their account." to
        "يمكن للمالك فقط تغيير الفرع الذي يُثبَّت عليه هذا الجهاز. اطلب من المالك إجراء التغيير من حسابه.",
    "This device's branch" to "فرع هذا الجهاز",
    "Choose which branch sales rung on this till are filed under." to
        "اختر الفرع الذي تُسجَّل تحته مبيعات هذا الصندوق.",
    "Falls back to the company's default branch" to "يعود إلى الفرع الافتراضي للشركة",
    "Save" to "حفظ",
    "This device is now pinned to %s" to "أصبح هذا الجهاز الآن مثبَّتًا على %s",
    "Branch pin cleared" to "تم إلغاء تثبيت الفرع",
    "Only the owner can change this device's branch. Ask the owner to make the change on their account." to
        "يمكن للمالك فقط تغيير فرع هذا الجهاز. اطلب من المالك إجراء التغيير من حسابه.",

    // ── Backup & restore (Wave 1A / Part G) ─────────────────────────────────────
    "Create backup" to "إنشاء نسخة احتياطية",
    "Restore backup" to "استعادة نسخة احتياطية",
    "Backup created" to "تم إنشاء النسخة الاحتياطية",
    "Restore this backup?" to "استعادة هذه النسخة الاحتياطية؟",
    "This will replace all current data on this device with the backup's data. This cannot be undone." to "سيؤدي هذا إلى استبدال جميع البيانات الحالية على هذا الجهاز ببيانات النسخة الاحتياطية. لا يمكن التراجع عن هذا.",
    "Restore complete. Restart the app to continue." to "اكتملت الاستعادة. أعد تشغيل التطبيق للمتابعة.",
    "Admin access required" to "مطلوب صلاحية المسؤول",
    "Only an administrator account can create or restore backups." to "يمكن لحساب المسؤول فقط إنشاء أو استعادة النسخ الاحتياطية.",
    "No backups yet" to "لا توجد نسخ احتياطية بعد",
    "Create a backup above to see it listed here." to "أنشئ نسخة احتياطية أعلاه لتظهر هنا.",
    "The backup could not be processed." to "تعذّرت معالجة النسخة الاحتياطية.",
    "Cancel" to "إلغاء",

    // ── Employees (Phase 1 — multi-device account model, design §3) ───────────
    // Roles are translated as job titles, not transliterated: "أمين صندوق" is
    // what the person's badge says in an Arabic-speaking shop, and a
    // transliterated "كاشير" would read as software jargon to the owner who
    // has to decide which of the two to assign somebody.
    "Team" to "الفريق",
    "Employees" to "الموظفون",
    // "Role" and "Try again" were already used by shipped screens (the
    // startup-error retry among them) with no entry here, so they rendered
    // English on an Arabic device. Added once, here, rather than per screen.
    "Role" to "الدور",
    "Try again" to "حاول مرة أخرى",
    "Accounts, roles & till PINs" to "الحسابات والأدوار وأرقام PIN للصندوق",
    "Owner" to "المالك",
    "Manager" to "مدير",
    "Cashier" to "أمين صندوق",
    "Active" to "نشط",
    "Deactivated" to "معطّل",
    "Invite pending" to "بانتظار قبول الدعوة",
    "Owner access required" to "مطلوب حساب المالك",
    "Only the owner account can create employees or change what they can do. " +
        "Ask the owner to make the change on their account." to
        "يمكن لحساب المالك فقط إنشاء الموظفين أو تغيير صلاحياتهم. اطلب من المالك إجراء التغيير من حسابه.",
    "Couldn't load employees" to "تعذّر تحميل الموظفين",
    "No employees yet" to "لا يوجد موظفون بعد",
    "Create an account for each person who works a till. They sign in with " +
        "their own email, so every sale is recorded against the person who rang it." to
        "أنشئ حسابًا لكل شخص يعمل على الصندوق. يسجّل كل منهم الدخول ببريده الخاص، فتُسجَّل كل عملية بيع باسم من نفّذها.",
    "Employee accounts live on this device until account sync is enabled." to
        "تبقى حسابات الموظفين على هذا الجهاز حتى يتم تفعيل مزامنة الحسابات.",
    "Add employee" to "إضافة موظف",
    "Work email" to "بريد العمل",
    "The account is identified by this email and an ID the app assigns " +
        "(EMP-0001, EMP-0002…). There is no separate name field." to
        "يُعرَّف الحساب بهذا البريد وبمعرّف يخصصه التطبيق (EMP-0001، EMP-0002…). لا يوجد حقل اسم منفصل.",
    "Send invite" to "إرسال الدعوة",
    // One fixed sentence covering both roles, not a per-role pair -- kept
    // character-for-character identical (English key) to the desktop
    // screen's copy of the same sentence (products/retail/frontend/
    // employees.js) so the two apps can never state this differently. See
    // EmployeesScreen.kt's roleExplainer() doc comment for why the previous
    // per-role pair here was replaced.
    "A cashier can sell, refund against a sale, and close their own drawer. A manager can also discount, adjust stock and read reports." to
        "يمكن لأمين الصندوق البيع والاسترداد مقابل عملية بيع وإغلاق صندوقه الخاص. ويمكن للمدير أيضًا تطبيق الخصم وتعديل المخزون وقراءة التقارير.",
    "The invite could not be created." to "تعذّر إنشاء الدعوة.",
    "Invite created" to "تم إنشاء الدعوة",
    "Give this code to the employee. They enter it once to choose their own password." to
        "أعطِ هذا الرمز للموظف. يُدخله مرة واحدة ليختار كلمة المرور الخاصة به.",
    "It works once, and it expires 7 days from now. After that, or after " +
        "they use it, create another invite." to
        "يعمل مرة واحدة، وتنتهي صلاحيته بعد 7 أيام من الآن. بعد ذلك، أو بعد استخدامه، أنشئ دعوة أخرى.",
    "The employee redeems it on a till running Aura, on the sign-in screen's " +
        "\"I have an invite code\" option." to
        "يستخدمه الموظف على صندوق يعمل بنظام Aura، من خيار «لديّ رمز دعوة» في شاشة تسجيل الدخول.",
    "Copy code" to "نسخ الرمز",
    "Invite code copied" to "تم نسخ رمز الدعوة",
    "Change role?" to "تغيير الدور؟",
    "Change role" to "تغيير الدور",
    "This resets their permissions to that role's defaults, including " +
        "any exception you granted them before." to
        "يعيد هذا ضبط صلاحياته إلى الإعدادات الافتراضية لذلك الدور، بما في ذلك أي استثناء منحته له سابقًا.",
    "They are signed out and sign back in with the new role." to
        "يتم تسجيل خروجه ثم يسجّل الدخول مجددًا بالدور الجديد.",
    "Role updated" to "تم تحديث الدور",
    "The change could not be saved." to "تعذّر حفظ التغيير.",
    "Deactivate account" to "تعطيل الحساب",
    "Reactivate account" to "إعادة تفعيل الحساب",
    "Account deactivated" to "تم تعطيل الحساب",
    "Account reactivated" to "تمت إعادة تفعيل الحساب",
    "They are signed out everywhere and cannot sign in again. Their past " +
        "sales stay on record. You can reactivate them later." to
        "يتم تسجيل خروجه من كل الأجهزة ولا يمكنه تسجيل الدخول مجددًا. تبقى مبيعاته السابقة مسجّلة. يمكنك إعادة تفعيله لاحقًا.",
    "Set till PIN" to "تعيين رمز PIN للصندوق",
    "Reset till PIN" to "إعادة تعيين رمز PIN للصندوق",
    "New 4-digit PIN" to "رمز PIN جديد من 4 أرقام",
    "PIN set" to "تم تعيين رمز PIN",
    "Save PIN" to "حفظ رمز PIN",
    "PIN saved" to "تم حفظ رمز PIN",
    "PIN removed" to "تمت إزالة رمز PIN",
    "Remove this PIN" to "إزالة رمز PIN هذا",
    // The four actions named here are `user_accounts.PASSWORD_ONLY_ACTIONS`,
    // not a plausible-sounding list. Refunds are deliberately NOT among them
    // (a cashier holds `retail.refund` by default and `create_return` is
    // sale-bound), so claiming they need the password would overstate the
    // protection to the one person deciding how much to trust a PIN.
    "A PIN says who is acting at the till. It does not grant permission — " +
        "voiding a closed sale, changing a price, managing employees and approving " +
        "a cash difference still ask for the password." to
        "يحدّد رمز PIN من يعمل على الصندوق. وهو لا يمنح أي صلاحية — فإلغاء فاتورة مقفلة وتغيير السعر وإدارة الموظفين واعتماد فروقات النقدية تطلب كلمة المرور دائمًا.",

    // Server-sent refusals from the /api/admin/employees routes. They are
    // rendered through apiErrorMessage(), which runs the server's own sentence
    // through tr() -- so without these entries an Arabic phone would show the
    // English original. Kept spelled EXACTLY as the backend raises them
    // (onboarding_routes.py / user_accounts.PinPolicyError); a reworded key
    // here silently stops matching and falls back to English.
    "Role must be manager or cashier." to "يجب أن يكون الدور مديرًا أو أمين صندوق.",
    "PIN must be exactly 4 digits." to "يجب أن يتكون رمز PIN من 4 أرقام بالضبط.",
    "Email already registered." to "البريد الإلكتروني مسجّل بالفعل.",
    "Email required" to "البريد الإلكتروني مطلوب",
    "User not found." to "المستخدم غير موجود.",
    "Admin only" to "للمالك فقط",
    "The owner account's role cannot be changed." to "لا يمكن تغيير دور حساب المالك.",
)
