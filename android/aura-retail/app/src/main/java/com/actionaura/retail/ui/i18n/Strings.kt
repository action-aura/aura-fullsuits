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
    "Back" to "رجوع",
    "Menu" to "القائمة",
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
    "Aura AI is coming soon ✨" to "Aura AI قريبًا ✨",
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
)
