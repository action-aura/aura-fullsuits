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
