package com.actionaura.retail.ui.localization

import androidx.compose.runtime.staticCompositionLocalOf

/**
 * M6.11 -- the shared, typed string-resolution authority. Real,
 * key-based lookup (never string concatenation for a translatable
 * sentence, M6.11's own explicit rule) with real parameterized-message
 * and simplified plural support.
 *
 * Real, curated catalog for THIS milestone's own real UI surface
 * (shell chrome + Category/Branch vertical slices, M6.16/M6.17) --
 * not a speculative full-app translation catalog. Every later
 * milestone's own screen adds its own real keys here as it is built,
 * the same discipline `ImportEntitySchemas.ENGLISH_ALIASES`'s own
 * "real, curated subset, not full legacy parity" scope decision
 * already established (M5.8).
 */
object AuraStrings {

    private val catalog: Map<String, Map<AppLocale, String>> = mapOf(
        "action.back" to mapOf(AppLocale.EN to "Back", AppLocale.AR to "رجوع"),
        "action.cancel" to mapOf(AppLocale.EN to "Cancel", AppLocale.AR to "إلغاء"),
        "action.retry" to mapOf(AppLocale.EN to "Retry", AppLocale.AR to "إعادة المحاولة"),
        "action.save" to mapOf(AppLocale.EN to "Save", AppLocale.AR to "حفظ"),
        "action.add" to mapOf(AppLocale.EN to "Add", AppLocale.AR to "إضافة"),
        "action.archive" to mapOf(AppLocale.EN to "Archive", AppLocale.AR to "أرشفة"),
        "action.reactivate" to mapOf(AppLocale.EN to "Reactivate", AppLocale.AR to "إعادة تفعيل"),
        "error.generic" to mapOf(AppLocale.EN to "Something went wrong", AppLocale.AR to "حدث خطأ ما"),
        "error.network" to mapOf(AppLocale.EN to "Couldn't reach the database", AppLocale.AR to "تعذر الوصول إلى قاعدة البيانات"),
        "error.duplicate_name" to mapOf(AppLocale.EN to "\"{0}\" already exists", AppLocale.AR to "\"{0}\" موجود بالفعل"),
        "category.title" to mapOf(AppLocale.EN to "Categories", AppLocale.AR to "الفئات"),
        "category.name" to mapOf(AppLocale.EN to "Category name", AppLocale.AR to "اسم الفئة"),
        "category.description" to mapOf(AppLocale.EN to "Description", AppLocale.AR to "الوصف"),
        "category.empty" to mapOf(AppLocale.EN to "No categories yet", AppLocale.AR to "لا توجد فئات بعد"),
        "branch.title" to mapOf(AppLocale.EN to "Branches", AppLocale.AR to "الفروع"),
        "branch.name" to mapOf(AppLocale.EN to "Branch name", AppLocale.AR to "اسم الفرع"),
        "branch.empty" to mapOf(AppLocale.EN to "No branches yet", AppLocale.AR to "لا توجد فروع بعد"),
        "branch.last_active_protected" to mapOf(AppLocale.EN to "The last active branch cannot be archived", AppLocale.AR to "لا يمكن أرشفة الفرع النشط الأخير"),
        "count.products.one" to mapOf(AppLocale.EN to "{0} product", AppLocale.AR to "منتج واحد"),
        "count.products.other" to mapOf(AppLocale.EN to "{0} products", AppLocale.AR to "{0} منتجات"),
        // M9.19 -- real activation flow catalog additions.
        "activation.welcome.title" to mapOf(AppLocale.EN to "Activate this device", AppLocale.AR to "تفعيل هذا الجهاز"),
        "activation.welcome.body" to mapOf(AppLocale.EN to "Sign in with your Aura account to activate this device.", AppLocale.AR to "سجّل الدخول بحسابك في Aura لتفعيل هذا الجهاز."),
        "activation.welcome.start" to mapOf(AppLocale.EN to "Get started", AppLocale.AR to "ابدأ"),
        "activation.signin.title" to mapOf(AppLocale.EN to "Sign in", AppLocale.AR to "تسجيل الدخول"),
        "activation.signin.email" to mapOf(AppLocale.EN to "Email", AppLocale.AR to "البريد الإلكتروني"),
        "activation.signin.password" to mapOf(AppLocale.EN to "Password", AppLocale.AR to "كلمة المرور"),
        "activation.signin.submit" to mapOf(AppLocale.EN to "Sign in", AppLocale.AR to "تسجيل الدخول"),
        "activation.verification.title" to mapOf(AppLocale.EN to "Verify your account", AppLocale.AR to "تحقق من حسابك"),
        "activation.verification.body" to mapOf(AppLocale.EN to "Check your email for a verification link before continuing.", AppLocale.AR to "تحقق من بريدك الإلكتروني للحصول على رابط التحقق قبل المتابعة."),
        "activation.license.title" to mapOf(AppLocale.EN to "Enter your License", AppLocale.AR to "أدخل الترخيص"),
        "activation.license.serial" to mapOf(AppLocale.EN to "License serial", AppLocale.AR to "الرقم التسلسلي للترخيص"),
        "activation.license.submit" to mapOf(AppLocale.EN to "Continue", AppLocale.AR to "متابعة"),
        "activation.policy.title" to mapOf(AppLocale.EN to "Device policy", AppLocale.AR to "سياسة الأجهزة"),
        "activation.policy.remaining" to mapOf(AppLocale.EN to "{0} of {1} device slots remaining", AppLocale.AR to "{0} من {1} فتحات أجهزة متبقية"),
        "activation.device.title" to mapOf(AppLocale.EN to "Name this device", AppLocale.AR to "سمِّ هذا الجهاز"),
        "activation.device.label" to mapOf(AppLocale.EN to "Device label", AppLocale.AR to "تسمية الجهاز"),
        "activation.confirm.title" to mapOf(AppLocale.EN to "Confirm activation", AppLocale.AR to "تأكيد التفعيل"),
        "activation.confirm.submit" to mapOf(AppLocale.EN to "Activate", AppLocale.AR to "تفعيل"),
        "activation.progress.title" to mapOf(AppLocale.EN to "Activating...", AppLocale.AR to "جارٍ التفعيل..."),
        "activation.device_limit.title" to mapOf(AppLocale.EN to "Device limit reached", AppLocale.AR to "تم الوصول إلى الحد الأقصى للأجهزة"),
        "activation.platform_unavailable.title" to mapOf(AppLocale.EN to "Platform unavailable", AppLocale.AR to "المنصة غير متاحة"),
        "activation.ios_not_ready.title" to mapOf(AppLocale.EN to "iOS activation not yet available", AppLocale.AR to "تفعيل iOS غير متاح بعد"),
        "activation.network_unavailable.title" to mapOf(AppLocale.EN to "No connection", AppLocale.AR to "لا يوجد اتصال"),
        "activation.not_configured.title" to mapOf(AppLocale.EN to "Activation service unavailable", AppLocale.AR to "خدمة التفعيل غير متاحة"),
        "activation.not_configured.body" to mapOf(AppLocale.EN to "This service isn't available in this build yet.", AppLocale.AR to "هذه الخدمة غير متاحة في هذا الإصدار بعد."),
        "activation.storage_unavailable.title" to mapOf(AppLocale.EN to "Secure storage unavailable", AppLocale.AR to "التخزين الآمن غير متاح"),
        "activation.storage_unavailable.body" to mapOf(AppLocale.EN to "This device can't securely store activation credentials yet.", AppLocale.AR to "لا يمكن لهذا الجهاز تخزين بيانات التفعيل بأمان بعد."),
        "activation.result.complete" to mapOf(AppLocale.EN to "Activation complete", AppLocale.AR to "اكتمل التفعيل"),
        "activation.support.title" to mapOf(AppLocale.EN to "Need help?", AppLocale.AR to "بحاجة إلى مساعدة؟"),
        "activation.support.action" to mapOf(AppLocale.EN to "Contact support", AppLocale.AR to "التواصل مع الدعم"),
    )

    /**
     * Real, parameterized resolution -- `{0}`, `{1}`, ... are replaced
     * positionally. A missing key falls back to the key itself (never
     * a crash, never silently blank) -- same real, disclosed "degrade
     * gracefully" precedent the audited legacy app's own `tr()`
     * function already established.
     */
    fun resolve(key: String, locale: AppLocale, args: List<String> = emptyList()): String {
        val template = catalog[key]?.get(locale) ?: catalog[key]?.get(AppLocale.EN) ?: key
        return args.foldIndexed(template) { index, acc, arg -> acc.replace("{$index}", arg) }
    }

    /**
     * Real, simplified plural resolution: English/Arabic both reduced
     * to a real two-way `one`/`other` split (`count == 1` -> `one`).
     * Real, disclosed simplification -- CLDR's full Arabic plural
     * category set (zero/one/two/few/many/other) is NOT implemented
     * this milestone; every real Arabic string in the catalog above
     * that needs pluralization only has `.one`/`.other` variants,
     * matching this real, narrower rule honestly rather than claiming
     * full CLDR compliance.
     */
    fun resolvePlural(baseKey: String, count: Long, locale: AppLocale, vararg args: String): String {
        val suffix = if (count == 1L) "one" else "other"
        return resolve("$baseKey.$suffix", locale, args.toList())
    }
}

val LocalAppLocale = staticCompositionLocalOf { AppLocale.EN }
