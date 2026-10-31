package com.actionaura.retail.licensing

import com.google.common.truth.Truth.assertThat
import org.junit.Test

/**
 * rc.19 (dress rehearsal 2026-10-31): the activation screen printed the raw plan code
 * "AURA_RETAIL_P1_YEARLY". The screen now shows the label Owner sends (plan_name), else a local
 * friendly name for the three launch codes, else nothing -- never the code itself.
 */
class PlanNameTest {
    private fun lic(code: String?, name: String? = null, nameAr: String? = null, label: String? = null) =
        AccountLicence("l1", label, code, name, nameAr, "TRIAL", "2026-11-09")

    @Test fun `a launch plan code maps to a friendly name in both languages`() {
        assertThat(planDisplayName(lic("AURA_RETAIL_P1_YEARLY"), false)).isEqualTo("Package 1 (yearly)")
        assertThat(planDisplayName(lic("AURA_RETAIL_P1_YEARLY"), true)).isEqualTo("الباقة 1 (سنوية)")
        assertThat(planDisplayName(lic("AURA_RETAIL_P2_MONTHLY_12"), false)).contains("Package 2")
        assertThat(planDisplayName(lic("AURA_RETAIL_P3_MONTHLY_24"), true)).contains("24")
    }

    @Test fun `a name sent by Owner wins over the local map`() {
        assertThat(planDisplayName(lic("AURA_RETAIL_P1_YEARLY", name = "Owner Name"), false)).isEqualTo("Owner Name")
        assertThat(planDisplayName(lic("AURA_RETAIL_P1_YEARLY", name = "Owner Name", nameAr = "اسم"), true)).isEqualTo("اسم")
    }

    @Test fun `an unknown code is never shown raw`() {
        assertThat(planDisplayName(lic("SOMETHING_NEW"), false)).isNull()
        assertThat(licenceDisplayLabel(lic("SOMETHING_NEW"), "your shop", false)).isEqualTo("your shop")
    }

    @Test fun `the label is the shop label then the package name`() {
        assertThat(licenceDisplayLabel(lic("AURA_RETAIL_P1_YEARLY", label = "Main shop"), "your shop", false))
            .isEqualTo("Main shop · Package 1 (yearly)")
        assertThat(licenceDisplayLabel(lic("AURA_RETAIL_P1_YEARLY"), "your shop", false))
            .isEqualTo("Package 1 (yearly)")
        assertThat(licenceDisplayLabel(lic("AURA_RETAIL_P1_YEARLY"), "Branch 2", false, alwaysFallback = true))
            .isEqualTo("Branch 2 · Package 1 (yearly)")
    }

    @Test fun `plan_name parses as a string or an en-ar map`() {
        assertThat(planNameOf("Package 1", "ar")).isEqualTo("Package 1")
        assertThat(planNameOf(mapOf("en" to "A", "ar" to "ب"), "ar")).isEqualTo("ب")
        assertThat(planNameOf(7, "en")).isNull()
        assertThat(planNameOf("  ", "en")).isNull()
    }
}
