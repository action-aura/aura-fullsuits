package com.actionaura.retail.ui.navigation

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class FeatureSurfaceRegistryTest {

    @Test
    fun everyRegistryEntryHasARealDistinctRoute() {
        val routes = FeatureSurfaceRegistry.entries.map { it.route }
        assertEquals(routes.distinct().size, routes.size, "no duplicate route entries")
    }

    @Test
    fun realAvailableEntriesMatchTheRealFourVerticalSlicesPlusShellChrome() {
        val available = FeatureSurfaceRegistry.entries.filter { it.availability == Availability.AVAILABLE }
        // Dashboard, More, Categories, Branches, Reports, ImportHome -- the
        // real, shipped M6 surfaces (Reports shares Dashboard's real screen).
        assertEquals(6, available.size)
    }

    @Test
    fun noEntryClaimsAvailabilityWithoutARealImplementationMilestone() {
        FeatureSurfaceRegistry.entries.filter { it.availability == Availability.AVAILABLE }.forEach { entry ->
            assertTrue(entry.implementationMilestone.startsWith("M6"), "${entry.route} claims AVAILABLE but cites '${entry.implementationMilestone}' as its real owning milestone")
        }
    }
}
