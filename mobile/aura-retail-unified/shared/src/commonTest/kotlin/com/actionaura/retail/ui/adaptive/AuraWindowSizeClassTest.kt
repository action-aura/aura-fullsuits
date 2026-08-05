package com.actionaura.retail.ui.adaptive

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/** M6.5 -- real, pure-function proof of the shared adaptive breakpoints, independent of any platform WindowSizeClass API. */
class AuraWindowSizeClassTest {

    @Test
    fun compactPhonePortraitWidthClassifiesAsCompact() {
        assertEquals(AuraWindowSizeClass.Compact, AuraWindowSizeClass.fromWidthDp(360))
    }

    @Test
    fun widthExactlyAtSixHundredIsMediumNotCompact() {
        assertEquals(AuraWindowSizeClass.Medium, AuraWindowSizeClass.fromWidthDp(600))
        assertEquals(AuraWindowSizeClass.Compact, AuraWindowSizeClass.fromWidthDp(599))
    }

    @Test
    fun widthExactlyAtEightHundredFortyIsExpandedNotMedium() {
        assertEquals(AuraWindowSizeClass.Expanded, AuraWindowSizeClass.fromWidthDp(840))
        assertEquals(AuraWindowSizeClass.Medium, AuraWindowSizeClass.fromWidthDp(839))
    }

    @Test
    fun tabletExpandedWidthClassifiesAsExpanded() {
        assertEquals(AuraWindowSizeClass.Expanded, AuraWindowSizeClass.fromWidthDp(1024))
    }

    @Test
    fun landscapeIsDerivedFromRealWidthVsHeightNeverAssumed() {
        val portrait = AuraWindowSize(360, 800)
        val landscape = AuraWindowSize(800, 360)
        assertFalse(portrait.isLandscape)
        assertTrue(landscape.isLandscape)
    }
}
