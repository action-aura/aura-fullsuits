package com.actionaura.retail.data

import com.actionaura.retail.financial.Money
import com.actionaura.retail.financial.PercentageRate
import com.actionaura.retail.financial.Quantity

/**
 * M5.1 -- every repository reads TEXT columns this codebase itself wrote
 * (database-schema-contract.md rule 1: money/quantity columns always hold
 * canonical `Money.toString()`/`Quantity.toString()` output). A parse
 * failure here means the local database's own data is corrupt, not a
 * normal validation-failure code path -- so these throw rather than
 * returning a RepositoryError, matching how a corrupt local SQLite file is
 * treated everywhere else in this codebase.
 */
internal fun parseStoredMoney(raw: String): Money =
    Money.parse(raw).getOrNull() ?: error("corrupt stored Money value: \"$raw\"")

internal fun parseStoredQuantity(raw: String): Quantity =
    Quantity.zeroOrMore(raw) ?: error("corrupt stored Quantity value: \"$raw\"")

internal fun parseStoredRate(raw: String): PercentageRate = PercentageRate.trusted(raw)
