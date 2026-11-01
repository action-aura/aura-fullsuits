package com.actionaura.retail.ui.i18n

import android.content.Context
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.actionaura.retail.ui.theme.AuraColors
import com.actionaura.retail.ui.theme.AuraPalette

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
 * The active till theme, one of [AuraPalette.ALL] (Day, Sand, Calm, Night,
 * Dusk -- owner request, 2026-09: "night mode back" + "themes for both mobile
 * and desktop"). Same persistence shape as [AppLocale] right above: a plain
 * String in the same "aura_prefs" SharedPreferences, restored before the
 * first composition and switched instantly through [AuraPalette.current]'s
 * observable Compose state -- no Activity restart, no per-screen wiring.
 */
object AppTheme {
    private const val PREFS = "aura_prefs"
    private const val KEY = "app_theme"

    /** Restores the saved theme, or Calm (this app's original palette) if none was ever saved. */
    fun load(ctx: Context) {
        val name = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, "dark")
        AuraPalette.current = AuraPalette.byName(name)
    }

    fun set(ctx: Context, palette: AuraColors) {
        AuraPalette.current = palette
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY, palette.name).apply()
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

/** The Arabic text for [en], or null if there is none -- lets a plain-JVM test check a translation exists
 *  without a Context (the active language is Compose state set from SharedPreferences). */
internal fun arabicFor(en: String): String? = AR_STRINGS[en]

private val AR_STRINGS: Map<String, String> = mapOf(
    // ── Sync: bootstrap (SYNC-BOOT Phase 3) ───────────────────────────────────────
    "This device could not install a copy of your shop's data after several tries, so it stopped asking. Please contact Aura support." to
        "تعذّر على هذا الجهاز تثبيت نسخة من بيانات متجرك بعد عدّة محاولات، فتوقّف عن الطلب. يُرجى التواصل مع دعم أورا.",
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
    "Quick actions" to "إجراءات سريعة",
    "New Sale" to "بيع جديد",
    "Share Receipt" to "مشاركة الإيصال",
    // Network-printer receipt printing (retail-hardware-viewports) --
    // see the "Retail settings" section below for the Settings-screen
    // strings this button's feature is configured through.
    "Print Receipt" to "طباعة الإيصال",
    "Couldn't print receipt" to "تعذّرت طباعة الإيصال",
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
    // Cart qty stepper icons (RetailScreens.kt) -- TalkBack was reading the
    // glyph ("−"/"+") instead of the action, since contentDescription carried
    // the literal symbol rather than a real label.
    "Decrease quantity" to "تقليل الكمية",
    "Increase quantity" to "زيادة الكمية",
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
    "Split" to "مقسّم",
    // The five payment-method names retail_api.py's _DEFAULT_METHODS seeds for every
    // company; a shop's own custom method name has no entry here and tr() falls back
    // to that name itself, which is fine since it was never translated either way.
    "Bank Transfer" to "تحويل بنكي",
    "Mobile Wallet" to "محفظة إلكترونية",
    "Check" to "شيك",
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

    // ── POS: loyalty point redemption (retail-hardware-viewports Android
    // wave; desktop parity, subsystem-retail.js's #pos-loyalty-redeem
    // section, schema v27) ─────────────────────────────────────────────────
    "Loyalty points" to "نقاط الولاء",
    "%d points available" to "%d نقطة متاحة",
    "Redeem points" to "استبدال النقاط",
    "Max" to "الحد الأقصى",
    "Redeemed value: %s off this sale" to "قيمة الاستبدال: %s خصم من هذا البيع",
    // Surfaced the moment a typed request exceeds what's actually redeemable,
    // instead of only being visible on the post-sale screen after the clamp.
    "Only %d points can be applied to this sale" to "يمكن تطبيق %d نقطة فقط على هذا البيع",
    "%d points redeemed (%s)" to "تم استبدال %d نقطة (%s)",

    // ── Shared API error mapping (net/ApiErrors.kt) ──────────────────────────
    // "This action is not available..." is flask_guard.py's fixed 403 message
    // text, translated verbatim so the licensing block reads natively in
    // Arabic instead of falling back to the English server string.
    "Blocked by your subscription/license:" to "محظور بسبب اشتراكك/ترخيصك:",
    "This action is not available in the current licensing state." to "هذا الإجراء غير متاح في حالة الترخيص الحالية.",
    // r17 (owner decision 10A): the clock-moved-back stop explains how to fix it. The English key is
    // flask_guard.py's message for CLOCK_REVIEW_REQUIRED (state_guidance.py), verbatim.
    "This device's date or time looks wrong -- it is earlier than a time it has already recorded -- so new sales are paused to protect your records. To fix it: set the correct date and time in this device's settings (turn on automatic date and time), then restart the app. Your data is safe, and reports, returns and backups still work." to
        "تاريخ هذا الجهاز أو وقته يبدو غير صحيح -- فهو أقدم من وقت سجّله الجهاز سابقًا -- لذلك أُوقفت المبيعات الجديدة لحماية سجلاتك. للحل: اضبط التاريخ والوقت الصحيحين من إعدادات هذا الجهاز (فعّل الضبط التلقائي للتاريخ والوقت)، ثم أعد تشغيل التطبيق. بياناتك بأمان، ويمكنك متابعة التقارير والمرتجعات والنسخ الاحتياطي.",
    // retail_api.py's BRANCH_LIMIT refusal (2026-09-06): the branch add-on is
    // enforced by the till, and apiErrorMessage() appends the server's own
    // sentence -- same key as products/retail/frontend/locales/ar.json.
    "Your licence has reached its branch limit. Ask Aura to add a branch." to "وصل ترخيصك إلى الحد الأقصى للفروع. اطلب من Aura إضافة فرع.",
    "Your session has expired. Please log in again." to "انتهت صلاحية جلستك. يرجى تسجيل الدخول مرة أخرى.",
    // ── Dashboard: cashier gate + failed-figures honesty (2026-09-06) --
    // same two keys, same sentences, as products/retail/frontend/locales/ar.json's
    // desktop "Ready to sell" card. See DashboardScreen.kt's gate comment.
    "Ready to sell" to "جاهز للبيع",
    "Sales totals and reports are limited to managers and the store owner. Open the till to start ringing sales." to
        "إجماليات المبيعات والتقارير متاحة للمديرين ومالك المتجر فقط. افتح نقطة البيع لبدء تسجيل المبيعات.",
    "Figures unavailable" to "الأرقام غير متاحة",
    "Could not load today's figures. Pull to refresh or check the connection." to
        "تعذّر تحميل أرقام اليوم. اسحب للتحديث أو تحقق من الاتصال.",
    "Till" to "نقطة البيع",

    // ── Join-existing-shop (ui/screens/JoinShopScreens.kt) -- same keys as
    // products/retail/frontend/locales/ar.json, where the desktop has the
    // same sentence.
    "Licence activated" to "تم تفعيل الترخيص",
    "Is your shop already set up on another device?" to "هل محلك مُجهَّز بالفعل على جهاز آخر؟",
    "Yes — connect to my shop" to "نعم — اربطني بمحلي",
    "No — this is the shop's first device" to "لا — هذا أول جهاز للمحل",
    "Connecting brings your existing owner and staff accounts to this phone. No new account is created."
        to "الربط يجلب حسابات المالك والموظفين الموجودة إلى هذا الهاتف. لا يُنشأ أي حساب جديد.",
    "Connecting to your shop…" to "جارٍ الاتصال بمحلك…",
    "Your account is on its way from your shop's other device." to "حسابك في الطريق من الجهاز الآخر لمحلك.",
    "Still waiting for your account. Check the connection, or set up a new shop instead."
        to "ما زلنا ننتظر حسابك. تحقق من الاتصال، أو أنشئ محلاً جديداً بدلاً من ذلك.",
    "Keep waiting" to "متابعة الانتظار",
    "Set up a new shop instead" to "إنشاء محل جديد بدلاً من ذلك",
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
    // The server's own literal from mt_auth.py's login handler (net/
    // ApiErrors.kt's loginErrorMessage), shown for a genuine wrong password
    // -- no equivalent string exists in the desktop catalogue
    // (products/retail/frontend/locales/ar.json has no login screen at all,
    // desktop sessions start pre-authenticated) to reuse, so this is a new
    // translation rather than a borrowed one.
    "Invalid email or password." to "البريد الإلكتروني أو كلمة المرور غير صحيحة.",
    "Couldn't reach the server. Try again." to "تعذّر الوصول إلى الخادم. حاول مرة أخرى.",
    "Sign in to your workspace" to "سجّل الدخول إلى مساحة عملك",
    "Email" to "البريد الإلكتروني",
    "Password" to "كلمة المرور",
    "Sign In" to "تسجيل الدخول",
    // The sign-in screen's redesign (2026-09-08, DESIGN.md §3 brand tagline +
    // a password-visibility toggle). "One shop. Every device." is the brand's
    // fixed tagline (§3) -- same English/Arabic pair everywhere it appears.
    "One shop. Every device." to "متجر واحد. كل الأجهزة.",
    "Show password" to "إظهار كلمة المرور",
    "Hide password" to "إخفاء كلمة المرور",
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
    // The cart's honesty line: the figure above it is pre-tax and
    // pre-discount, and used to be labelled "Total" while the server charged
    // something else. See PosScreen's cart summary for the full reasoning.
    "Tax and discounts are applied at checkout" to "تُضاف الضريبة والخصومات عند الدفع",
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
    // Aseel-parity wave A-PAR (schema v32): a bounced/cancelled cheque's
    // reversal row on the customer/supplier statement (StatementSheet /
    // SupplierStatementSheet in RetailExtraScreens.kt).
    "Cheque returned" to "شيك مرتجع",
    // The two return-settlement buckets that move customers.credit_balance
    // without a `payments` row -- customer_statement now emits them as
    // kinds 'return_forgiven' and 'return_credit' (retail_api.py). Wording
    // reuses this catalog's established terms rather than inventing any:
    // مستحقات for receivables and رصيد لدى المتجر for store credit, both
    // already used by the desktop catalog's own refund-settlement toasts.
    "Credit forgiven" to "إعفاء من المستحقات",
    "Store credit issued" to "رصيد صادر لدى المتجر",
    // v44: the one-off opening line a computer upgraded to v44 carries so the
    // balance it already showed stays the same (statement kinds
    // balance_carried_owed / balance_carried_credit).
    "Balance carried forward (owed)" to "رصيد مرحَّل (مستحق عليه)",
    "Balance carried forward (credit)" to "رصيد مرحَّل (دائن له)",
    "Payment" to "دفعة",
    "Void" to "إلغاء",
    "Record a payment" to "تسجيل دفعة",
    "Amount" to "المبلغ",
    "Enter a valid amount" to "أدخل مبلغًا صحيحًا",
    "Failed" to "فشل",
    "Pay" to "دفع",
    // Supplier payment "Paid from" choice (MONEY-3 M2) -- wording matches the desktop locale.
    "Paid from" to "مدفوع من",
    "Till drawer" to "درج الصندوق",
    "Office cash (not from a drawer)" to "نقد المكتب (ليس من الدرج)",
    "No cash drawer is open on this computer." to "لا يوجد درج نقدي مفتوح على هذا الجهاز.",
    "There is no open cash drawer on this device. Open the drawer first, or pay from the office." to
        "لا يوجد درج نقدي مفتوح على هذا الجهاز. افتح الدرج أولًا، أو ادفع من المكتب.",

    // ── Categories screen ──────────────────────────────────────────────
    // The whole screen (ui/screens/CategoriesScreen.kt) routes every visible
    // string through tr(), and 11 of its 12 keys had no entry here -- only
    // "Couldn't save" did -- so an Arabic till reached it from a fully
    // translated More-hub row ("الفئات") and then rendered the entire screen
    // in English. Exactly the silent tr() fallback the "Categories" entry in
    // the More-hub section above was added for, one screen further in.
    // TranslationCatalogueCoverageTest now scans EVERY screen for this shape
    // rather than one screen at a time.
    "No categories yet" to "لا توجد فئات بعد",
    "Group products so they're easy to find and filter." to "جمّع المنتجات ليسهل العثور عليها وتصفيتها.",
    "Add Category" to "إضافة فئة",
    "Edit Category" to "تعديل فئة",
    "Category added" to "تمت إضافة الفئة",
    "Category updated" to "تم تحديث الفئة",
    "Category name *" to "اسم الفئة *",
    "Category name is required" to "اسم الفئة مطلوب",
    "Description" to "الوصف",
    "Save Category" to "حفظ الفئة",
    // Rendered as "${c.product_count} " + tr("items") -- Arabic counts a bare
    // singular after a numeral here, the same form "%d item(s)" already uses
    // in the POS held-sales list.
    "items" to "صنف",

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

    // ── Retail settings → Receipt Printer (retail-hardware-viewports) ─────────
    "Receipt Printer" to "طابعة الإيصالات",
    // PRINT-2: the old "network only / English only" notes were true until this
    // wave; the receipt is now drawn as a picture, so Arabic prints.
    "Choose how this phone prints receipts. A Bluetooth printer must be paired in Android settings first." to
        "اختر طريقة طباعة الإيصالات من هذا الهاتف. يجب إقران طابعة البلوتوث أولاً من إعدادات أندرويد.",
    "Printer IP address" to "عنوان IP الخاص بالطابعة",
    "Port" to "المنفذ",
    "Paper width" to "عرض الورق",
    "80mm (42 chars)" to "80 مم (42 حرفًا)",
    "58mm (32 chars)" to "58 مم (32 حرفًا)",
    "Auto-print after each sale" to "الطباعة التلقائية بعد كل عملية بيع",
    "Prints automatically as soon as a sale completes, with no extra tap." to
        "تتم الطباعة تلقائيًا فور اكتمال البيع، دون الحاجة لأي لمسة إضافية.",
    // Receipts print as a picture (drawn on the phone), which is why Arabic is
    // correct on every printer brand. Not yet checked on real printers.
    "Receipts print as a picture, so Arabic prints correctly. Always do a test print on your own printer before relying on it." to
        "تُطبع الإيصالات كصورة، لذلك تظهر العربية بشكل صحيح. أجرِ طباعة تجريبية على طابعتك قبل الاعتماد عليها.",
    "Printer type" to "نوع الطابعة",
    "Built-in" to "مدمجة",
    "Bluetooth" to "بلوتوث",
    "USB" to "USB",
    "Network" to "الشبكة",
    "None" to "بدون",
    "Choose a paired printer" to "اختر طابعة مقترنة",
    "No paired Bluetooth devices. Pair the printer in Android settings first." to
        "لا توجد أجهزة بلوتوث مقترنة. أقرن الطابعة من إعدادات أندرويد أولاً.",
    "Allow Nearby devices to list paired printers." to "اسمح بالأجهزة القريبة لعرض الطابعات المقترنة.",
    "Choose a USB printer" to "اختر طابعة USB",
    "No USB printer is plugged in." to "لا توجد طابعة USB موصولة.",
    "Refresh" to "تحديث",
    "Selected" to "المحدد",
    "Test print" to "طباعة تجريبية",
    "Printed the test receipt" to "تمت طباعة الإيصال التجريبي",
    "Open cash drawer (test)" to "فتح درج النقود (تجربة)",
    "Sent the drawer signal" to "تم إرسال إشارة فتح الدرج",
    "Open the drawer after a cash sale" to "فتح الدرج بعد البيع النقدي",
    "Only after a cash sale printed automatically. A reprint never opens it." to
        "فقط بعد بيع نقدي يُطبع تلقائيًا. إعادة الطباعة لا تفتحه أبدًا.",
    "The built-in printer of this terminal." to "الطابعة المدمجة في هذا الجهاز.",
    "Save printer settings" to "حفظ إعدادات الطابعة",

    // ── Retail settings → Shop network (retail-hardware-viewports) ─────────────
    // Wording deliberately mirrors site-relay.js's philosophy: nothing here
    // names a relay, a key type, or an installation id -- the person reading
    // owns a shop and needs the consequence, not the mechanism.
    "Shop network" to "شبكة المتجر",
    "This phone syncs with your shop over its own network." to
        "يتزامن هذا الهاتف مع متجرك عبر شبكته الخاصة.",
    "Connected to" to "متصل بـ",
    "Disconnect" to "قطع الاتصال",
    "Disconnected" to "تم قطع الاتصال",
    "Connect this phone to your shop's till network to keep sales in sync, even when the internet is down." to
        "قم بتوصيل هذا الهاتف بشبكة صندوق متجرك لإبقاء المبيعات متزامنة، حتى عند انقطاع الإنترنت.",
    "Paste the text from the till here" to "الصق النص من جهاز الصندوق هنا",
    "Connect" to "اتصال",
    "This device needs to be activated before it can connect to a shop." to
        "يجب تفعيل هذا الجهاز قبل أن يتمكن من الاتصال بمتجر.",
    "That code has already been used or is not valid" to "تم استخدام هذا الرمز من قبل أو أنه غير صالح",
    "That code has expired — ask for a new one on the till" to
        "انتهت صلاحية هذا الرمز — اطلب رمزًا جديدًا من جهاز الصندوق",
    "Couldn't connect to the shop's till. Try again." to "تعذّر الاتصال بصندوق المتجر. حاول مرة أخرى.",
    "Couldn't verify the till's identity. Get a fresh code from the till and try again." to
        "تعذّر التحقق من هوية جهاز الصندوق. احصل على رمز جديد من الصندوق وحاول مرة أخرى.",
    "Couldn't reach the till. Check the connection and try again." to
        "تعذّر الوصول إلى جهاز الصندوق. تحقق من الاتصال وحاول مرة أخرى.",
    "This device is connected and will sync with the shop's till." to
        "تم توصيل هذا الجهاز وسيتم مزامنته مع صندوق المتجر.",
    "That pairing text isn't valid." to "هذا النص غير صالح للاتصال.",

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

    // ── ANDROID-1007: partial PO receive + stock transfers ───────────────────
    // NOTE: a native Arabic speaker should confirm all of these; they were
    // written by the builder, not reviewed by one.
    "Pending" to "قيد الانتظار",
    "Partial" to "مستلم جزئيًا",
    "Received" to "تم الاستلام",
    "Closed" to "مغلق",
    "Cancelled" to "ملغى",
    "Ordered" to "المطلوب",
    "Remaining" to "المتبقي",
    "Receive now" to "استلام الآن",
    "Receive all" to "استلام الكل",
    "Enter a quantity to receive" to "أدخل كمية للاستلام",
    "Enter a valid quantity" to "أدخل كمية صحيحة",
    "Close short" to "إغلاق بالمستلم",
    "Close short?" to "إغلاق بالمستلم؟",
    "The rest of this order will not be received. This cannot be undone." to
        "لن يتم استلام باقي هذا الأمر. لا يمكن التراجع عن ذلك.",
    "You cannot receive more than is still on order." to "لا يمكنك استلام أكثر مما تبقى في الأمر.",
    "This hasn't finished syncing from your other device yet. Try again in a moment." to
        "لم تكتمل مزامنة هذا من جهازك الآخر بعد. حاول مرة أخرى بعد قليل.",
    "Only an order that is partly received can be closed short." to
        "لا يمكن الإغلاق بالمستلم إلا للأمر المستلم جزئيًا.",
    "This order needs a supplier before it can be closed short." to
        "يحتاج هذا الأمر إلى مورد قبل إغلاقه بالمستلم.",
    "Stock Transfers" to "تحويلات المخزون",
    "Move stock between branches" to "نقل المخزون بين الفروع",
    "No stock transfers" to "لا توجد تحويلات مخزون",
    "Move stock from one branch to another." to "انقل المخزون من فرع إلى آخر.",
    "Stock transfers will show here." to "ستظهر تحويلات المخزون هنا.",
    "New Transfer" to "تحويل جديد",
    "Transfer created" to "تم إنشاء التحويل",
    "From branch" to "من فرع",
    "To branch" to "إلى فرع",
    "Choose both branches" to "اختر الفرعين",
    "The two branches must be different" to "يجب أن يكون الفرعان مختلفين",
    "Create Transfer" to "إنشاء تحويل",
    "Send this transfer?" to "إرسال هذا التحويل؟",
    "Cancel this transfer?" to "إلغاء هذا التحويل؟",
    "Recall this transfer?" to "استرجاع هذا التحويل؟",
    "The stock leaves the source branch now." to "ستخرج البضاعة من الفرع المرسل الآن.",
    "Nothing has been sent, so no stock moves." to "لم يُرسل شيء، لذا لن يتحرك أي مخزون.",
    "The stock goes back to the source branch." to "ستعود البضاعة إلى الفرع المرسل.",
    "Confirm" to "تأكيد",
    "Couldn't load this transfer." to "تعذّر تحميل هذا التحويل.",
    "Sent" to "المرسل",
    "Came up short" to "وصل أقل من المرسل",
    "Cancel transfer" to "إلغاء التحويل",
    "Recall" to "استرجاع",
    "Enter how many of each item actually arrived. Use 0 if none did." to
        "أدخل كم وصل فعليًا من كل صنف. اكتب 0 إن لم يصل شيء.",
    "Enter a valid quantity for every item" to "أدخل كمية صحيحة لكل صنف",
    "All" to "الكل",
    "In transit" to "قيد النقل",

    // ── Settings (main) ───────────────────────────────────────────────────────
    "Appearance" to "المظهر",
    "Theme" to "السمة",
    "System default" to "افتراضي النظام",
    // The five sanctioned till themes (owner request, 2026-09: "night mode
    // back" + "themes for both mobile and desktop") -- same names and same
    // choice as the desktop's theme switcher, see ui/theme/Color.kt's
    // AuraPalette. "Choose theme" mirrors "Choose language" right below.
    "Day" to "نهاري",
    "Sand" to "رملي",
    "Calm" to "هادئ",
    "Night" to "ليلي",
    "Dusk" to "غسق",
    "Choose theme" to "اختر المظهر",
    "Language" to "اللغة",
    "Notifications" to "الإشعارات",
    "Reminders & alerts" to "التذكيرات والتنبيهات",
    "Security" to "الأمان",
    "Biometric lock" to "القفل البيومتري",
    "Unlock with fingerprint" to "إلغاء القفل ببصمة الإصبع",
    "Change password" to "تغيير كلمة المرور",
    "Backup & restore" to "النسخ الاحتياطي والاستعادة",
    "Save a copy…" to "حفظ نسخة…",
    "Backup saved" to "تم حفظ النسخة الاحتياطية",
    "Share" to "مشاركة",
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
    // Shown beside rows that are still worth reading when a background refresh
    // failed -- the list is the last one this device could actually read, not
    // a wrong one. See EmployeesScreen.refreshQuietly.
    "Showing the last list this device could read — " to
        "يتم عرض آخر قائمة تمكّن هذا الجهاز من قراءتها — ",
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
    // Registry v9 (Track S): a person can carry a display name and a named job
    // (preset). The old "no separate name field" sentence is gone with the field.
    "Name" to "الاسم",
    "Name (optional)" to "الاسم (اختياري)",
    // The two name refusals the server can give (text_safety.clean_single_line).
    // A blank name is NOT a refusal: it clears the name.
    "Name must be 60 characters or fewer." to "يجب ألا يزيد الاسم على 60 حرفًا.",
    "Name must be a single line." to "يجب أن يكون الاسم سطرًا واحدًا.",
    "Unknown preset." to "وظيفة غير معروفة.",
    "Role does not match the chosen preset." to "الدور لا يطابق الوظيفة المختارة.",
    "Custom" to "مخصّص",
    "Branch" to "الفرع",
    "There is no branch to choose yet." to "لا يوجد فرع للاختيار بعد.",
    "You don't have permission to do this." to "ليست لديك صلاحية لتنفيذ هذا الإجراء.",
    // Empty-state lines shown instead of an "Add ..." call to action when the
    // signed-in person does not hold the capability the server needs for the
    // write (RetailSession.hasAnyCapability).
    "Returns will show here." to "ستظهر المرتجعات هنا.",
    "Suppliers will show here." to "سيظهر الموردون هنا.",
    "Purchase orders will show here." to "ستظهر أوامر الشراء هنا.",
    "Categories will show here." to "ستظهر التصنيفات هنا.",
    "Save name" to "حفظ الاسم",
    "Name saved" to "تم حفظ الاسم",
    "Branch manager" to "مدير فرع",
    "Accountant" to "محاسب",
    "Everything a cashier can do, plus discounts, stock changes and reports." to
        "كل ما يستطيعه أمين الصندوق، إضافة إلى الخصومات وتعديل المخزون والتقارير.",
    "A manager who can also add and manage staff, for one branch only." to
        "مدير يستطيع أيضًا إضافة الموظفين وإدارتهم، لفرع واحد فقط.",
    "Reads reports and the books, records entries and approves cash differences. Cannot sell at the till." to
        "يقرأ التقارير والدفاتر، ويسجّل القيود، ويعتمد فروقات النقد. لا يستطيع البيع على جهاز البيع.",
    "Can sell, refund against a sale and close their own drawer." to
        "يستطيع البيع والاسترداد مقابل عملية بيع وإغلاق صندوقه الخاص.",
    "Choose a branch for this person before making them a branch manager." to
        "اختر فرعًا لهذا الشخص قبل تعيينه مدير فرع.",
    "Change job?" to "تغيير الوظيفة؟",
    "Change job" to "تغيير الوظيفة",
    "Job updated" to "تم تحديث الوظيفة",
    // Books-only home (Accountant): AppRoot.kt BooksHomeScreen.
    "Books" to "الدفاتر",
    "You can read the books and reports here. Selling at the till is not part of this account." to
        "يمكنك هنا قراءة الدفاتر والتقارير. البيع على جهاز البيع ليس من صلاحيات هذا الحساب.",
    "Every account, debits against credits." to "كل الحسابات، المدين مقابل الدائن.",
    "Income minus cost and expenses for a period." to "الإيراد ناقص التكلفة والمصروفات لفترة معيّنة.",
    "What the business owns and owes." to "ما يملكه النشاط وما عليه.",
    "Sales and stock reports." to "تقارير المبيعات والمخزون.",
    "What customers owe." to "ما يدين به العملاء.",
    "What the shop owes suppliers." to "ما يدين به المتجر للموردين.",
    "Daily cash in and out." to "النقد الداخل والخارج يوميًا.",
    "How overdue each balance is." to "مدى تأخر كل رصيد.",
    "This resets their permissions to that job's defaults, including " +
        "any exception you granted them before." to
        "يعيد هذا ضبط صلاحياته إلى الإعدادات الافتراضية لتلك الوظيفة، بما في ذلك أي استثناء منحته له سابقًا.",
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

    // ── Sync status screen ──────────────────────────────────────────────────
    // MoreScreen's new "Device" section and the screen it opens
    // (ui/screens/SyncStatusScreen.kt). `SyncCoordinator.health()` existed,
    // tested, with no reader until this screen -- these are every literal it
    // introduces. `tr()` falls back to English on a missing key, which is
    // exactly why this block is not optional: a gap here is silent.
    "Device" to "الجهاز",
    "Sync status" to "حالة المزامنة",
    // The permanent Licence doorway in More -> Device. Added after a real
    // handset was found stranded in LOCAL_STATE_CORRUPT with no route to
    // activation anywhere in the UI.
    "Licence" to "الترخيص",
    "Activation, status and device registration" to "التفعيل والحالة وتسجيل الجهاز",
    "Whether this device is reaching your others" to "هل يصل هذا الجهاز إلى أجهزتك الأخرى",
    "Sync is not set up" to "المزامنة غير مُفعّلة",
    "This build has no relay address, so this device never sends or receives. Other devices will not see its sales." to
        "لا يحتوي هذا الإصدار على عنوان مُرحّل، لذا لا يرسل هذا الجهاز أو يستقبل أي بيانات أبدًا. لن تظهر مبيعاته على الأجهزة الأخرى.",
    "Waiting for first sync" to "بانتظار أول مزامنة",
    "Subscription unpaid — you can keep selling. Sync and updates are paused until you renew." to
        "الاشتراك غير مدفوع — يمكنك متابعة البيع. المزامنة والتحديثات متوقفة مؤقتاً حتى تجدّد الاشتراك.",
    "Subscription unpaid" to "الاشتراك غير مدفوع",
    "Trial ended" to
        "انتهت التجربة",
    "Free trial" to
        "التجربة المجانية",
    "Trial: 1 day left" to
        "التجربة: متبقي يوم واحد",
    "Trial: 2 days left" to
        "التجربة: متبقي يومان",
    "Trial: %s days left" to
        "التجربة: متبقي %s أيام",
    "Your free trial has ended" to
        "انتهت تجربتك المجانية",
    "Your data is safe. You can still look at your records and reports, and make a backup or an export. Selling is locked until you pay." to
        "بياناتك آمنة. لا يزال بإمكانك الاطلاع على سجلاتك وتقاريرك، وإنشاء نسخة احتياطية أو تصدير. البيع مقفل حتى تقوم بالدفع.",
    "Back up and share my data" to
        "انسخ بياناتي احتياطياً وشاركها",
    "How to unlock" to
        "كيفية فتح القفل",
    "Contact your Aura seller, or pay by reference number when available." to
        "تواصل مع بائع Aura الخاص بك، أو ادفع برقم مرجعي عند توفر ذلك.",
    "After paying, connect to the internet and the app unlocks automatically." to
        "بعد الدفع، اتصل بالإنترنت وسيُفتح التطبيق تلقائياً.",
    "Sync paused — subscription unpaid" to "المزامنة متوقفة — الاشتراك غير مدفوع",
    "You can keep selling. Sales are saved on this device and will sync after you renew." to
        "يمكنك متابعة البيع. تُحفظ المبيعات على هذا الجهاز وستتم مزامنتها بعد تجديد الاشتراك.",
    "Set up, but this device has not reached the relay yet." to "تم الإعداد، لكن هذا الجهاز لم يصل إلى المُرحّل بعد.",
    "Sync is failing" to "المزامنة تفشل",
    "This device is not reaching your other devices." to "لا يصل هذا الجهاز إلى أجهزتك الأخرى.",
    "consecutive failures" to "محاولات فاشلة متتالية",
    "Sync is working" to "المزامنة تعمل",
    "Last synced" to "آخر مزامنة",
    "waiting to send" to "بانتظار الإرسال",
    "Detail" to "التفاصيل",
    "Sending" to "الإرسال",
    "Receiving" to "الاستلام",
    "Healthy" to "سليم",
    // The third sync half-state. "Healthy" only after a real round trip has
    // succeeded; a half that is running but has never completed one is
    // Waiting, not healthy.
    "Waiting" to "بالانتظار",
    "Failing" to "فاشل",
    "Last success" to "آخر نجاح",
    "never" to "أبدًا",
    "just now" to "الآن",
    "s ago" to " ثانية مضت",
    "m ago" to " دقيقة مضت",
    "h ago" to " ساعة مضت",
    "d ago" to " يوم مضت",

    // ── Trial Balance (retail-hardware-viewports, schema v38/v39 general
    //    ledger) -- ui/screens/TrialBalanceScreen.kt. Where a string also
    //    appears on the desktop (products/retail/frontend/locales/{en,ar}.json,
    //    the "Trial Balance"/"Ties" section), the Arabic here is copied
    //    VERBATIM from that already-vetted catalogue rather than re-written,
    //    so a shop switching between the till and the desktop reads the same
    //    words for the same figures. ─────────────────────────────────────────
    "Trial Balance" to "ميزان المراجعة",
    "Chart of accounts and the AR/AP tie-back" to "شجرة الحسابات ومطابقة الذمم المدينة والدائنة",
    "Only accounts with reports access can see the trial balance. " +
        "Ask the owner to grant it." to
        "يمكن فقط للحسابات التي تملك صلاحية التقارير الاطّلاع على ميزان المراجعة. اطلب من المالك منحك هذه الصلاحية.",
    "Couldn't load the trial balance" to "تعذّر تحميل ميزان المراجعة",
    "Something went wrong loading this report." to "حدث خطأ أثناء تحميل هذا التقرير.",
    "Balanced" to "متوازن",
    "Out of Balance" to "غير متوازن",
    "Total debits equal total credits." to "إجمالي المدين يساوي إجمالي الدائن.",
    "Total debits and total credits do not match. This needs attention." to
        "إجمالي المدين وإجمالي الدائن غير متطابقين. يتطلب هذا الانتباه.",
    // NOTHING_POSTED -- the state the desktop does not distinguish from
    // Balanced (see trialBalanceStatus()'s doc comment): both totals at
    // exactly zero, a brand-new shop's freshly-seeded chart of accounts.
    "Nothing posted yet" to "لم يتم ترحيل أي حركة بعد",
    "No sales, purchases or payments have posted to the ledger yet." to
        "لم تُرحَّل أي مبيعات أو مشتريات أو دفعات إلى دفتر الأستاذ بعد.",
    "Subledger Reconciliation" to "تسوية الدفاتر المساعدة",
    "GL Accounts Receivable" to "المستحقات في دفتر الأستاذ العام",
    "GL Accounts Payable" to "المستحقات الدائنة في دفتر الأستاذ العام",
    "Customer Balances (Subledger)" to "أرصدة العملاء (الدفتر المساعد)",
    "Supplier Balances (Subledger)" to "أرصدة الموردين (الدفتر المساعد)",
    "Ties" to "متطابق",
    "Does Not Tie" to "غير متطابق",
    // UNKNOWN -- this client's own third tie state (arTieState/apTieState's
    // doc comment): a missing/malformed subledger, never defaulted to either
    // "Ties" or "Does Not Tie".
    "Couldn't verify" to "تعذّر التحقق",
    "Accounts" to "الحسابات",
    "Debit Amount" to "المبلغ المدين",
    "Credit Amount" to "المبلغ الدائن",
    "No accounts posted yet" to "لا توجد حسابات مرحّلة بعد",
    "Transactions will appear here once the general ledger starts recording them." to
        "ستظهر الحركات هنا بمجرد أن يبدأ دفتر الأستاذ العام بتسجيلها.",
    // account_type labels (accountTypeLabel()) -- 'revenue' deliberately
    // reuses "Income"/"إيراد" rather than a new word; see that function's
    // doc comment for why.
    "Asset" to "أصل",
    "Liability" to "خصوم",
    "Equity" to "حقوق الملكية",
    "Income" to "إيراد",
    "Expense" to "مصروف",
    "Other" to "أخرى",

    // ── Profit & Loss / Balance Sheet (retail-hardware-viewports,
    //    core/retail/financial_statements.py) -- ui/screens/
    //    ProfitAndLossScreen.kt, BalanceSheetScreen.kt. Every string that
    //    also appears on the desktop (products/retail/frontend/locales/
    //    {en,ar}.json's own Profit and Loss / Balance Sheet sections) is
    //    copied VERBATIM from that already-vetted catalogue, same reasoning
    //    and same discipline the Trial Balance section above states for
    //    itself -- so a shop switching between the till and the desktop
    //    reads the same words for the same figures. The few keys with no
    //    desktop equivalent (the two MoreItem subtitles, the four gate/
    //    error strings) are this screen's own, written to match the exact
    //    phrasing pattern the Trial Balance section already established for
    //    the identical situation ("Only accounts with reports access can
    //    see the trial balance..." / "Couldn't load the trial balance").
    //    Checked against ui/i18n/Strings.kt's existing "Credit"/"آجل" trap
    //    before writing any of these -- none of them touch that word. ──────
    "Profit and Loss" to "الأرباح والخسائر",
    "Revenue, cost of goods and net profit for a period" to "الإيرادات وتكلفة البضاعة وصافي الربح لفترة معينة",
    "Balance Sheet" to "الميزانية العمومية",
    "Assets, liabilities and equity as of a date" to "الأصول والخصوم وحقوق الملكية حتى تاريخ معيّن",
    "Only accounts with reports access can see the profit and loss statement. " +
        "Ask the owner to grant it." to
        "يمكن فقط للحسابات التي تملك صلاحية التقارير الاطّلاع على قائمة الأرباح والخسائر. اطلب من المالك منحك هذه الصلاحية.",
    "Only accounts with reports access can see the balance sheet. " +
        "Ask the owner to grant it." to
        "يمكن فقط للحسابات التي تملك صلاحية التقارير الاطّلاع على الميزانية العمومية. اطلب من المالك منحك هذه الصلاحية.",
    "Couldn't load the profit and loss statement" to "تعذّر تحميل قائمة الأرباح والخسائر",
    "Couldn't load the balance sheet" to "تعذّر تحميل الميزانية العمومية",
    "As of Date" to "كما في تاريخ",
    "Gross Revenue" to "إجمالي الإيرادات",
    "Sales Returns" to "مرتجعات المبيعات",
    "Cost of Goods Sold" to "تكلفة البضاعة المباعة",
    "Gross Margin" to "هامش الربح الإجمالي",
    "Operating Expenses" to "المصروفات التشغيلية",
    "No operating expenses recorded in this period." to "لا توجد مصروفات تشغيلية مسجّلة في هذه الفترة.",
    "Net Profit" to "صافي الربح",
    // The cost-basis honesty flag -- income_statement's own module docstring
    // point 5. TWO different warning bodies for the SAME root cause (this
    // statement's own numbers vs. the balance sheet's), matching
    // financial_statements.py's own distinction -- see CostBasisWarningCard's
    // doc comment.
    "Cost basis is incomplete for this period" to "أساس التكلفة غير مكتمل لهذه الفترة",
    "sale(s) affected" to "عملية بيع متأثرة",
    "These sales have no recorded cost of goods. Cost of goods sold is understated, " +
        "and the gross profit and net profit shown below are overstated." to
        "لا تحمل عمليات البيع هذه أي تكلفة مسجّلة للبضاعة، لذلك فإن تكلفة البضاعة المباعة أقل من الواقع، " +
        "وإجمالي الربح وصافي الربح الموضّحان أدناه أعلى من الواقع الفعلي.",
    "Cost basis is incomplete" to "أساس التكلفة غير مكتمل",
    "These sales have no recorded cost of goods. Inventory and retained earnings " +
        "shown below are overstated." to
        "لا تحمل عمليات البيع هذه أي تكلفة مسجّلة للبضاعة، لذلك فإن المخزون والأرباح المحتجزة الموضّحين " +
        "أدناه أعلى من الواقع الفعلي.",
    // NOTHING_POSTED, for a whole period/as-of-date -- the same "zeros are
    // not the same claim as nothing recorded" instinct TrialBalanceStatus's
    // own NOTHING_POSTED state carries.
    "Nothing was posted in this period" to "لم يتم ترحيل أي شيء في هذه الفترة",
    "No sale, return, purchase or expense was recorded in this date range. " +
        "This is not a zero profit -- there is nothing to measure yet." to
        "لم يتم تسجيل أي عملية بيع أو إرجاع أو شراء أو مصروف خلال هذا النطاق الزمني. " +
        "هذا لا يعني ربحًا صفريًا -- فلا يوجد بعد ما يمكن قياسه.",
    "Nothing has been posted as of this date" to "لم يتم ترحيل أي شيء حتى هذا التاريخ",
    "No sale, return, purchase or expense was recorded on or before this date. " +
        "This is not a zero balance sheet -- there is nothing to measure yet." to
        "لم يتم تسجيل أي عملية بيع أو إرجاع أو شراء أو مصروف في هذا التاريخ أو قبله. " +
        "هذا لا يعني ميزانية عمومية صفرية -- فلا يوجد بعد ما يمكن قياسه.",
    "Assets" to "الأصول",
    "No asset accounts recorded as of this date." to "لا توجد حسابات أصول مسجَّلة حتى هذا التاريخ.",
    "Liabilities" to "الخصوم",
    "No liability accounts recorded as of this date." to "لا توجد حسابات خصوم مسجَّلة حتى هذا التاريخ.",
    "No equity accounts recorded as of this date." to "لا توجد حسابات حقوق ملكية مسجَّلة حتى هذا التاريخ.",
    "Retained Earnings" to "الأرباح المحتجزة",
    "Accumulated profit since the ledger began. This product has no period-close " +
        "step, so this figure is always current rather than a posted ledger entry." to
        "أرباح متراكمة منذ بداية دفتر الأستاذ العام. لا يتضمن هذا المنتج خطوة إقفال للفترة المحاسبية، " +
        "لذلك يبقى هذا الرقم محدَّثًا دائمًا بدلاً من أن يكون قيدًا مُرحَّلاً في دفتر الأستاذ.",
    "Total Equity" to "إجمالي حقوق الملكية",
    "Total Assets" to "إجمالي الأصول",
    "Total Liabilities and Equity" to "إجمالي الخصوم وحقوق الملكية",
    "Difference" to "الفرق",

    // ── Settings → Updates (updates/UpdateCoordinator.kt, ui/screens/UpdatesSection.kt) ──
    // Every error sentence is plain on purpose: the person reading it runs a
    // shop, not a server. "Owner" never appears -- it is the update server.
    "Updates" to "التحديثات",
    "This version" to "هذا الإصدار",
    "Check for updates" to "التحقق من وجود تحديثات",
    "Checking for updates…" to "جارٍ التحقق من وجود تحديثات…",
    "You have the latest version." to "لديك أحدث إصدار.",
    "A new version is available: %s" to "يتوفر إصدار جديد: %s",
    "Download size: %s MB" to "حجم التنزيل: %s ميغابايت",
    "This update is required." to "هذا التحديث مطلوب.",
    "Update now" to "حدّث الآن",
    "Install now" to "ثبّت الآن",
    "Open Settings" to "فتح الإعدادات",
    "Downloading the update…" to "جارٍ تنزيل التحديث…",
    "The update is downloaded and checked. Press the button to install it." to
        "تم تنزيل التحديث والتحقق منه. اضغط الزر لتثبيته.",
    "Android needs your permission before this app can install updates. Allow it in Settings, then come back and press Install now." to
        "يحتاج أندرويد إلى إذنك قبل أن يتمكن هذا التطبيق من تثبيت التحديثات. اسمح بذلك من الإعدادات ثم عد واضغط ثبّت الآن.",
    "Installing… follow the Android prompt on the screen." to "جارٍ التثبيت… اتبع رسالة أندرويد على الشاشة.",
    "Updates are not set up on this build." to "التحديثات غير مفعّلة في هذا الإصدار.",
    "Activate this device first, then updates can be checked." to "فعّل هذا الجهاز أولًا، ثم يمكن التحقق من التحديثات.",
    "Couldn't reach the update server. Check the internet connection and try again." to
        "تعذّر الوصول إلى خادم التحديثات. تحقق من اتصال الإنترنت وحاول مرة أخرى.",
    "The app couldn't check for updates just now. Try again in a moment." to
        "تعذّر على التطبيق التحقق من التحديثات الآن. حاول مرة أخرى بعد قليل.",
    "This update could not be proven genuine, so it was not used." to "تعذّر إثبات أصالة هذا التحديث، لذلك لم يُستخدم.",
    "The update details didn't match this device, so they were not used." to
        "لم تتطابق تفاصيل التحديث مع هذا الجهاز، لذلك لم تُستخدم.",
    "The update details were incomplete or wrong, so they were not used." to
        "كانت تفاصيل التحديث ناقصة أو خاطئة، لذلك لم تُستخدم.",
    "The update server says this device can't get updates right now. Check the licence." to
        "يفيد خادم التحديثات بأن هذا الجهاز لا يستطيع تلقي التحديثات حاليًا. تحقق من الترخيص.",
    "The update server didn't allow the download. Try again later." to "لم يسمح خادم التحديثات بالتنزيل. حاول لاحقًا.",
    "The download failed. Check the internet connection and try again." to
        "فشل التنزيل. تحقق من اتصال الإنترنت وحاول مرة أخرى.",
    "The downloaded file was damaged, so it was deleted. Try again." to
        "كان الملف الذي تم تنزيله تالفًا، لذلك حُذف. حاول مرة أخرى.",
    "This update was not signed by Action Aura, so it was not installed." to
        "لم يتم توقيع هذا التحديث من قبل أكشن أورا، لذلك لم يُثبَّت.",
    "This update is for a different app, so it was not installed." to "هذا التحديث مخصص لتطبيق آخر، لذلك لم يُثبَّت.",
    "This update is not newer than the version you already have." to "هذا التحديث ليس أحدث من الإصدار الذي لديك.",
    "The downloaded file changed before it could be installed, so it was deleted. Try again." to
        "تغيّر الملف الذي تم تنزيله قبل تثبيته، لذلك حُذف. حاول مرة أخرى.",
    "Android couldn't finish the install. Try again." to "تعذّر على أندرويد إكمال التثبيت. حاول مرة أخرى.",
    "Something went wrong with the update. Try again." to "حدث خطأ في التحديث. حاول مرة أخرى.",
    "Discount %" to
        "نسبة الخصم %",
    "Discount on the whole sale (%)" to
        "خصم على كامل الفاتورة (%)",
    "Up to %s%%" to
        "حتى %s%%",
    "You can give at most %s%% discount. Ask a manager for more." to
        "يمكنك منح خصم بحد أقصى %s%%. اطلب من المدير لمنح خصم أكبر.",
    "Set" to
        "تعيين",
    "Clear" to
        "مسح",
    "Hold This Sale" to
        "تعليق هذه الفاتورة",
    "The cart is parked and cleared here so you can start a new sale. Resume it later from Held sales." to
        "تُحفظ السلة وتُفرَّغ هنا لتبدأ فاتورة جديدة. يمكنك استئنافها لاحقًا من المبيعات المعلّقة.",
    "Note (optional)" to
        "ملاحظة (اختياري)",
    "e.g. Table 4, customer name" to
        "مثال: طاولة 4، اسم العميل",
    "Hold Sale" to
        "تعليق الفاتورة",
    "A held sale is kept on this device's shop server, so any till can resume it." to
        "تُحفظ الفاتورة المعلّقة على خادم المحل، فيمكن لأي نقطة بيع استئنافها.",
    "Sale held" to
        "تم تعليق الفاتورة",
    "Could not hold sale" to
        "تعذّر تعليق الفاتورة",
    "Could not resume sale" to
        "تعذّر استئناف الفاتورة",
    "Resume Held Sale" to
        "استئناف فاتورة معلّقة",
    "Your current cart is not empty. Resuming will replace it with the held sale. Continue?" to
        "السلة الحالية ليست فارغة. الاستئناف سيستبدلها بالفاتورة المعلّقة. هل تتابع؟",
    "Discard Held Sale" to
        "تجاهل فاتورة معلّقة",
    "Discard this held sale? This cannot be undone." to
        "هل تريد تجاهل هذه الفاتورة المعلّقة؟ لا يمكن التراجع عن ذلك.",
    "Held sale discarded" to
        "تم تجاهل الفاتورة المعلّقة",
    "Resumed" to
        "تم الاستئناف",
    "Split payment" to
        "دفع مجزّأ",
    "Pay with one method" to
        "الدفع بطريقة واحدة",
    "Estimated amount due" to
        "المبلغ المستحق المقدّر",
    "Tax and promotions are confirmed at checkout. If the final amount differs, the till will tell you what is missing." to
        "تُحسب الضريبة والعروض عند إتمام البيع. إذا اختلف المبلغ النهائي فستبيّن لك الصندوق ما ينقص.",
    "Remove part" to
        "إزالة الدفعة",
    "Add part" to
        "إضافة دفعة",
    "Rest" to
        "الباقي",
    "Add the first payment part." to
        "أضف أول دفعة.",
    "A sale can be paid in at most 6 parts." to
        "يمكن دفع الفاتورة على 6 دفعات كحد أقصى.",
    "The parts do not cover the amount due yet." to
        "الدفعات لا تغطي المبلغ المستحق بعد.",
    "The part on account is more than what is left to pay." to
        "الجزء على الحساب أكبر من المتبقي للدفع.",
    "Only cash can be more than what is due. Lower the card or other amount." to
        "النقد وحده يمكن أن يزيد عن المستحق. خفّض مبلغ البطاقة أو الطريقة الأخرى.",
    "The cash part would all come back as change. Remove it." to
        "الجزء النقدي سيعود كله كباقٍ. أزله.",
    "Add a second part to split, or turn split payment off." to
        "أضف دفعة ثانية للتجزئة، أو أوقف الدفع المجزّأ.",
    "Cash Drawer" to
        "درج النقد",
    "Open a shift, cash in and out, end the shift" to
        "فتح وردية، إدخال وإخراج النقد، وإنهاء الوردية",
    "Could not load the cash drawer. Its state is unknown." to
        "تعذّر تحميل درج النقد. حالته غير معروفة.",
    "Retry" to
        "إعادة المحاولة",
    "Open Cash Shift" to
        "فتح وردية نقدية",
    "This drawer belongs to" to
        "هذا الدرج يخص",
    "Another till in this shop has a drawer open." to
        "هناك نقطة بيع أخرى في هذا المحل لديها درج مفتوح.",
    "Opening Float" to
        "رصيد افتتاح الدرج",
    "Filled in from the last count on this terminal. Change it if the drawer has changed since." to
        "تمت تعبئته من آخر عدّ لهذا الجهاز. غيّره إذا تغيّر محتوى الدرج منذ ذلك الحين.",
    "Enter a valid opening float" to
        "أدخل رصيد افتتاح صحيحًا",
    "Cash shift opened" to
        "تم فتح الوردية النقدية",
    "Failed to open shift" to
        "تعذّر فتح الوردية",
    "Open Shift" to
        "فتح الوردية",
    "Shift open" to
        "الوردية مفتوحة",
    "Opening float" to
        "رصيد الافتتاح",
    "Opened" to
        "وقت الفتح",
    "Cash In / Cash Out" to
        "إدخال / إخراج نقد",
    "X Report" to
        "تقرير X",
    "End Shift" to
        "إنهاء الوردية",
    "Cash sales" to
        "مبيعات نقدية",
    "Customer payments (cash)" to
        "دفعات العملاء (نقدًا)",
    "Cash refunds" to
        "مرتجعات نقدية",
    "Float in" to
        "إيداع في الدرج",
    "Float out" to
        "سحب من الدرج",
    "Paid in" to
        "مقبوضات أخرى",
    "Paid out" to
        "مدفوعات أخرى",
    "Expected cash in drawer" to
        "النقد المتوقع في الدرج",
    "The expected cash is shown to the person who approves the count." to
        "يظهر النقد المتوقع لمن يعتمد العدّ.",
    "Waiting for approval" to
        "بانتظار الاعتماد",
    "Force-closed, never counted" to
        "أُغلقت تلقائيًا دون عدّ",
    "Expected cash" to
        "النقد المتوقع",
    "Counted Cash (physical count)" to
        "النقد المعدود (العدّ الفعلي)",
    "Variance (counted − expected)" to
        "الفرق (المعدود − المتوقع)",
    "Variance accepted." to
        "تم قبول الفرق.",
    "Failed to accept the variance" to
        "تعذّر قبول الفرق",
    "Acknowledge uncounted" to
        "الإقرار بعدم العدّ",
    "Accept variance" to
        "قبول الفرق",
    "Shift ended and variance accepted." to
        "انتهت الوردية وتم قبول الفرق.",
    "Shift ended. The variance is recorded and awaiting approval." to
        "انتهت الوردية. سُجّل الفرق وهو بانتظار الاعتماد.",
    "Failed to end shift" to
        "تعذّر إنهاء الوردية",
    "Cash movement recorded" to
        "تم تسجيل حركة النقد",
    "Failed to record movement" to
        "تعذّر تسجيل الحركة",
    "Float In (add cash to drawer)" to
        "إيداع في الدرج (إضافة نقد)",
    "Float Out (remove cash from drawer)" to
        "سحب من الدرج (إخراج نقد)",
    "Paid In (misc. cash received)" to
        "مقبوضات أخرى (نقد مستلم)",
    "Paid Out (misc. cash paid)" to
        "مدفوعات أخرى (نقد مدفوع)",
    "Cash Movement" to
        "حركة نقدية",
    "What for?" to
        "لأي غرض؟",
    "Choose what the money was for" to
        "اختر الغرض من المبلغ",
    "Optional note" to
        "ملاحظة اختيارية",
    "Record" to
        "تسجيل",
    "End Shift — Z Report" to
        "إنهاء الوردية — تقرير Z",
    "{n} held sale(s) are still parked. Ending the shift keeps them, but check them before you leave." to
        "{n} فاتورة معلّقة ما زالت محفوظة. إنهاء الوردية يُبقيها، لكن راجعها قبل أن تغادر.",
    "Ending locks this count. A short drawer never blocks you from ending your shift. The variance is recorded and someone with approval authority reviews it." to
        "الإنهاء يثبّت هذا العدّ. نقص الدرج لا يمنعك من إنهاء ورديتك. يُسجَّل الفرق ويراجعه من لديه صلاحية الاعتماد.",
    "Rent" to
        "الإيجار",
    "Utilities" to
        "الكهرباء والماء والخدمات",
    "Salaries and wages" to
        "الرواتب والأجور",
    "Transport and delivery" to
        "النقل والتوصيل",
    "Marketing" to
        "التسويق",
    "Bank and card fees" to
        "رسوم البنك والبطاقات",
    "Other expenses" to
        "مصاريف أخرى",
    "Owner took it home (drawings)" to
        "أخذه المالك (مسحوبات المالك)",
    "Other income" to
        "دخل آخر",
    "Owner put money back (drawings)" to
        "أعاده المالك إلى الدرج (مسحوبات المالك)",
    "New total" to
        "الإجمالي الجديد",
    "Round total down to" to
        "تقريب الإجمالي للأسفل إلى",
    "Damaged" to
        "تالف",
    "Loyal customer" to
        "عميل دائم",
    "Round down" to
        "تقريب للأسفل",
    "Discount %s  →  Estimated total %s" to
        "الخصم %s  ←  الإجمالي المقدّر %s",
    "An estimate. The shop confirms the real total when you charge." to
        "هذا تقدير. يؤكد المحل الإجمالي الفعلي عند الدفع.",
    "Remove discount" to
        "إزالة الخصم",
    "Apply discount" to
        "تطبيق الخصم",
    "Enter a discount first" to
        "أدخل الخصم أولاً",
    "Pick a reason first" to
        "اختر السبب أولاً",
    "The discount cannot be more than the cart total before tax." to
        "لا يمكن أن يزيد الخصم عن إجمالي السلة قبل الضريبة.",
    "The new total cannot be higher than the current total." to
        "لا يمكن أن يكون الإجمالي الجديد أعلى من الإجمالي الحالي.",
    "Ask a manager" to
        "اطلب موافقة المدير",
    "A manager has to enter their PIN to allow this." to
        "على المدير إدخال رمز PIN الخاص به للسماح بذلك.",
    "Approve" to
        "موافقة",
    "Choose a variant" to
        "اختر النوع",
    "No variants found." to
        "لا توجد أنواع.",
    "Out of stock" to
        "نفد من المخزون",
    "Choose options" to
        "اختر الإضافات",
    "Required" to
        "مطلوب",
    "Optional" to
        "اختياري",
    "Choose one" to
        "اختر واحدًا",
    "Choose at least" to
        "اختر على الأقل",
    "Choose up to" to
        "اختر حتى",
    "Choose any" to
        "اختر ما تشاء",
    "Add to cart" to
        "أضف إلى السلة",
    "Item discount (%)" to
        "خصم الصنف (%)",
    "Price" to
        "السعر",
    "A different price needs a manager's PIN at Charge." to
        "السعر المختلف يحتاج رمز PIN من المدير عند الدفع.",
    "The price cannot be negative." to
        "لا يمكن أن يكون السعر سالبًا.",
    "Apply" to
        "تطبيق",
    "Discount cleared — the cart changed" to
        "أُزيل الخصم — تغيّرت السلة",
    "Discount cleared — it cannot be combined with an item discount or promotion" to
        "أُزيل الخصم — لا يمكن جمعه مع خصم صنف أو عرض",
    "The sale discount is not kept with a held sale." to
        "لا يُحفظ خصم الفاتورة مع الفاتورة المعلّقة.",
    "No manager has a PIN set up. Ask the owner to set one in Settings > Team." to
        "لا يوجد مدير لديه رمز PIN. اطلب من المالك تعيينه من الإعدادات > الفريق.",
    "The total charged is %s" to
        "الإجمالي المحصَّل هو %s",
    "Remove the item discounts and promotions first" to
        "أزل خصومات الأصناف والعروض أولاً",
    "Estimated total" to
        "الإجمالي المقدّر",
    "was" to
        "كان",
    "Price changed" to
        "تم تغيير السعر",
    "Discount / price" to
        "خصم / سعر",
    "%d variants" to
        "%d أنواع",
    "That manager PIN was not accepted." to
        "لم يُقبل رمز PIN الخاص بالمدير.",
    "Too many wrong PINs for this manager. Try again in a few minutes." to
        "محاولات خاطئة كثيرة لرمز PIN هذا المدير. حاول بعد بضع دقائق.",
    "A different person must approve this. Pick a manager." to
        "يجب أن يوافق شخص آخر. اختر مديرًا.",
    "You are not allowed to change a price. Ask a manager to approve it." to
        "غير مسموح لك بتغيير السعر. اطلب موافقة المدير.",
    "Say why this discount is given (for example Damaged, Loyal customer, Round down)." to
        "اذكر سبب هذا الخصم (مثل: تالف، عميل دائم، تقريب للأسفل).",
    "Send either a discount amount or a target total, not both." to
        "أرسل مبلغ خصم أو إجمالي مطلوبًا، وليس كليهما.",
    "A cart discount cannot be combined with a per-item discount or a quotation. Remove one of them." to
        "لا يمكن جمع خصم السلة مع خصم صنف أو عرض سعر. أزل أحدهما.",
    "That exact total cannot be reached. Nearest: %s" to
        "لا يمكن الوصول إلى هذا الإجمالي بالضبط. الأقرب: %s",
    "That exact total cannot be reached. Pick one of the nearby totals." to
        "لا يمكن الوصول إلى هذا الإجمالي بالضبط. اختر أحد الإجماليات القريبة.",
    "That exact discount cannot be given on this cart. Try a slightly different amount." to
        "لا يمكن منح هذا الخصم بالضبط على هذه السلة. جرّب مبلغًا مختلفًا قليلًا.",
    "That discount does not fit this cart." to
        "هذا الخصم لا يناسب هذه السلة.",
    // ── Sign in with your Aura account + "which computer is this?" (new customer flow, plan S6;
    // ui/screens/AccountSignInScreen.kt, RoleStepScreen.kt). Same sentences as the desktop's
    // products/retail/frontend/locales/ar.json. Arabic written by the assistant -- native speaker to confirm.
    "Sign in with your Aura account to activate this computer. Use the email and password your seller gave you." to
        "سجّل الدخول بحساب Aura لتفعيل هذا الجهاز. استخدم البريد الإلكتروني وكلمة المرور اللذين أعطاك إياهما البائع.",
    "Sign in" to
        "تسجيل الدخول",
    "The email or password is not right. Check them and try again." to
        "البريد الإلكتروني أو كلمة المرور غير صحيحة. تحقق منهما وحاول مرة أخرى.",
    "Too many attempts. Please wait a few minutes and try again." to
        "محاولات كثيرة جدًا. انتظر بضع دقائق ثم حاول مرة أخرى.",
    "This version of Aura Retail is too old to start a trial. Please download the newest version from actionaura.me and install it." to
        "هذا الإصدار من Aura Retail قديم جدًا لبدء التجربة. حمّل أحدث إصدار من actionaura.me وثبّته.",
    "The agreement text could not be checked, so it was not shown. Please try again, or contact support." to
        "تعذّر التحقق من نص العقد فلم يتم عرضه. حاول مرة أخرى أو تواصل مع الدعم.",
    "Your sign-in timed out. Please sign in again." to
        "انتهت مهلة تسجيل الدخول. سجّل الدخول مرة أخرى.",
    "The agreement must be accepted before this computer can be activated." to
        "يجب قبول العقد قبل تفعيل هذا الجهاز.",
    "This computer could not be activated with that account. Sign in again, or contact your seller." to
        "تعذّر تفعيل هذا الجهاز بهذا الحساب. سجّل الدخول مرة أخرى أو تواصل مع البائع.",
    "This license has no free phone slot. Deactivate a device or contact support to add an extra phone." to
        "لا يوجد مكان شاغر لهاتف في هذا الترخيص. عطّل جهازًا أو تواصل مع الدعم لإضافة هاتف إضافي.",
    "This account has reached its computer limit. Contact your seller to add a device." to
        "وصل هذا الحساب إلى الحد الأقصى للأجهزة. تواصل مع البائع لإضافة جهاز.",
    "That password is not allowed. Choose a longer one you have not used before." to
        "كلمة المرور هذه غير مسموحة. اختر كلمة أطول لم تستخدمها من قبل.",
    "The new password must be at least 10 characters." to
        "يجب أن تتكوّن كلمة المرور الجديدة من 10 أحرف على الأقل.",
    "The new password must be different from your temporary password." to
        "يجب أن تختلف كلمة المرور الجديدة عن كلمة المرور المؤقتة.",
    "Choose your own password" to
        "اختر كلمة مرورك الخاصة",
    "The password your seller gave you was temporary. Choose a new one that only you know. At least 10 characters." to
        "كلمة المرور التي أعطاك إياها البائع مؤقتة. اختر كلمة مرور جديدة لا يعرفها غيرك، 10 أحرف على الأقل.",
    "New password" to
        "كلمة المرور الجديدة",
    "Repeat the new password" to
        "أعد كتابة كلمة المرور الجديدة",
    "Save password" to
        "حفظ كلمة المرور",
    "The two passwords are not the same." to
        "كلمتا المرور غير متطابقتين.",
    "One last step: please read and accept the service agreement." to
        "خطوة أخيرة: يرجى قراءة عقد الخدمة والموافقة عليه.",
    "Read the agreement to the end, then tick the box to continue." to
        "اقرأ العقد حتى نهايته ثم ضع علامة في المربع للمتابعة.",
    "I have read the agreement and I accept it" to
        "قرأت العقد وأوافق عليه",
    "I agree that Action Aura keeps the photos of my ID and commercial registration to verify my account (deleted one year after my subscription ends)." to
        "أوافق على احتفاظ Action Aura بصورتي هويتي وسجلي التجاري للتحقق من حسابي (تُحذف بعد سنة من انتهاء اشتراكي).",
    "I agree that my account and shop data are stored on Action Aura's servers outside Jordan (Germany and Cloudflare)." to
        "أوافق على حفظ بيانات حسابي ومتجري على خوادم Action Aura خارج الأردن (ألمانيا وCloudflare).",
    "Without this, our team will verify your identity by phone." to
        "بدون هذه الموافقة سيتحقق فريقنا من هويتك هاتفيًا.",
    "The shop owner (or their authorised person) must accept personally; the seller may not accept on their behalf." to
        "يجب أن يوافق صاحب المتجر أو المفوَّض بنفسه، ولا يجوز للمندوب الموافقة نيابة عنه.",
    "Please tick the box about storing data outside Jordan to continue." to
        "يرجى الموافقة على حفظ البيانات خارج الأردن للمتابعة.",
    "Scroll to the end of the agreement to enable the box." to
        "مرّر إلى نهاية العقد لتفعيل المربع.",
    "Accept" to
        "أوافق",
    "Decline and go back" to
        "رفض والعودة",
    "Which branch is this computer for?" to
        "لأي فرع هذا الجهاز؟",
    "trial ends" to
        "تنتهي التجربة في",
    "Continue" to
        "متابعة",
    "Signed in. This computer is ready to be activated for:" to
        "تم تسجيل الدخول. هذا الجهاز جاهز للتفعيل لـ:",
    "your shop" to
        "متجرك",
    "Your free trial runs until" to
        "تجربتك المجانية مستمرة حتى",
    "Activate this computer" to
        "تفعيل هذا الجهاز",
    "Your request was received and is waiting for approval from Action Aura." to
        "تم استلام طلبك وهو بانتظار موافقة Action Aura.",
    "Use a license key" to
        "استخدام مفتاح الترخيص",
    "Which computer is this?" to
        "أي جهاز هذا؟",
    "Choose once. You can add more computers later." to
        "اختر مرة واحدة. يمكنك إضافة أجهزة أخرى لاحقًا.",
    "This is the boss computer (Admin)" to
        "هذا جهاز المدير (الإدارة)",
    "The boss computer sets up the shop, the staff and the settings. Every shop needs one." to
        "جهاز المدير يُعدّ المتجر والموظفين والإعدادات. كل متجر يحتاج إلى جهاز واحد منه.",
    "This is a cashier computer" to
        "هذا جهاز كاشير",
    "A cashier computer sells at the counter. It gets its staff from the boss computer." to
        "جهاز الكاشير يبيع عند الصندوق. يحصل على الموظفين من جهاز المدير.",

)
