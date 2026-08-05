package com.actionaura.retail.ui.localization

/**
 * M6.11 -- the shared locale authority. Real, deliberate two-locale
 * scope this milestone (English + Arabic), matching the real, already-
 * shipping legacy app's own bilingual support
 * (`presentation-authority-audit.md`'s own confirmed finding) -- not
 * invented from nothing.
 */
enum class AppLocale(val tag: String, val nativeName: String) {
    EN("en", "English"),
    AR("ar", "العربية");

    val isRtl: Boolean get() = this == AR
}
