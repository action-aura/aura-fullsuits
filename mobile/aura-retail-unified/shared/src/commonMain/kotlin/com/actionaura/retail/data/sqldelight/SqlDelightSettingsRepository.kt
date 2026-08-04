package com.actionaura.retail.data.sqldelight

import com.actionaura.retail.data.SettingsRepository
import com.actionaura.retail.db.RetailDatabase

/** M5.1 -- real, SQLDelight-backed `SettingsRepository`. */
class SqlDelightSettingsRepository(private val db: RetailDatabase) : SettingsRepository {

    override suspend fun getSetting(companyId: Long, key: String): String? =
        db.settingsQueries.selectSetting(companyId, key).executeAsOneOrNull()

    override suspend fun getAllSettings(companyId: Long): Map<String, String> =
        db.settingsQueries.selectAllSettings(companyId).executeAsList().associate { it.skey to it.svalue }

    override suspend fun setSetting(companyId: Long, key: String, value: String) {
        db.settingsQueries.upsertSetting(companyId, key, value)
    }

    override suspend fun nextDocumentNumber(companyId: Long, docType: String): Long = db.transactionWithResult {
        // Lock-fresh read-increment-write inside one transaction -- two
        // concurrent sales on the same company/docType must never receive
        // the same sequence number (same TOCTOU discipline as
        // BranchRepository.setActive / InventoryRepository.adjustStock).
        val current = db.settingsQueries.selectDocSequence(companyId, docType).executeAsOneOrNull() ?: 0L
        val next = current + 1
        db.settingsQueries.upsertDocSequence(companyId, docType, next)
        next
    }
}
