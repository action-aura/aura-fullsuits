package com.actionaura.retail.ui.localization

import kotlin.test.Test
import kotlin.test.assertEquals

class AuraStringsTest {

    @Test
    fun realKeyResolvesToTheRealEnglishStringByDefault() {
        assertEquals("Categories", AuraStrings.resolve("category.title", AppLocale.EN))
    }

    @Test
    fun realKeyResolvesToTheRealArabicStringWhenArabicIsSelected() {
        assertEquals("الفئات", AuraStrings.resolve("category.title", AppLocale.AR))
    }

    @Test
    fun aMissingKeyFallsBackToTheKeyItselfNeverACrash() {
        assertEquals("some.unknown.key", AuraStrings.resolve("some.unknown.key", AppLocale.EN))
    }

    @Test
    fun parameterizedMessagesSubstitutePositionally() {
        assertEquals("\"Beverages\" already exists", AuraStrings.resolve("error.duplicate_name", AppLocale.EN, listOf("Beverages")))
        assertEquals("\"مشروبات\" موجود بالفعل", AuraStrings.resolve("error.duplicate_name", AppLocale.AR, listOf("مشروبات")))
    }

    @Test
    fun pluralResolutionPicksOneForExactlyOneAndOtherOtherwise() {
        assertEquals("1 product", AuraStrings.resolvePlural("count.products", 1L, AppLocale.EN, "1"))
        assertEquals("5 products", AuraStrings.resolvePlural("count.products", 5L, AppLocale.EN, "5"))
        assertEquals("0 products", AuraStrings.resolvePlural("count.products", 0L, AppLocale.EN, "0"))
    }

    @Test
    fun arLocaleIsRealRtlEnIsNot() {
        assertEquals(true, AppLocale.AR.isRtl)
        assertEquals(false, AppLocale.EN.isRtl)
    }
}
