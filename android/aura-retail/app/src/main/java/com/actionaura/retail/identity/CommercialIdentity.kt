package com.actionaura.retail.identity

import android.content.Context
import com.actionaura.retail.BuildConfig
import java.util.UUID

/**
 * Commercial-identity metadata placeholders (Phase 4G of the Android migration).
 *
 * This object exists so future phases (Owner Control Center, licensing, telemetry)
 * have a single, stable place to read per-install identity from -- it does NOT
 * implement licensing, activation, or telemetry itself, and no real customer_id,
 * tenant_id, or license key is embedded here or anywhere else in this build.
 *
 * - productCode      stable, hardcoded ("AURA_RETAIL")
 * - installationId   random UUID, generated once on first run, persisted locally;
 *                    identifies THIS install, not a customer or license
 * - tenantId         placeholder only; null until a future licensing phase assigns one
 * - environment       "debug" / "staging" / "release" (BuildConfig.BUILD_ENV)
 * - appVersion        BuildConfig.VERSION_NAME
 * - schemaVersion      local placeholder for the future update/migration framework
 */
object CommercialIdentity {

    private const val PREFS = "aura_retail_identity"
    private const val KEY_INSTALLATION_ID = "installation_id"

    const val PRODUCT_CODE: String = BuildConfig.PRODUCT_CODE
    const val SCHEMA_VERSION: Int = 1

    val environment: String get() = BuildConfig.BUILD_ENV
    val appVersion: String get() = BuildConfig.VERSION_NAME

    /** Not yet assigned -- reserved for a future licensing/Owner-platform phase. */
    val tenantId: String? = null

    fun installationId(context: Context): String {
        val prefs = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        prefs.getString(KEY_INSTALLATION_ID, null)?.let { return it }
        val generated = UUID.randomUUID().toString()
        prefs.edit().putString(KEY_INSTALLATION_ID, generated).apply()
        return generated
    }
}
