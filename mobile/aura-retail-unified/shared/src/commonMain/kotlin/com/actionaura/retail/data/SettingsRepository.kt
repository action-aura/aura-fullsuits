package com.actionaura.retail.data

/** M5.1 -- typed plumbing over `retail_settings`/`doc_sequences` (Settings.sq). */
interface SettingsRepository {
    suspend fun getSetting(companyId: Long, key: String): String?
    suspend fun getAllSettings(companyId: Long): Map<String, String>
    suspend fun setSetting(companyId: Long, key: String, value: String)

    /** Atomically increments and returns the next sequence number for a document type (e.g. "sale", "return") -- doc_sequences' sole purpose. */
    suspend fun nextDocumentNumber(companyId: Long, docType: String): Long
}
