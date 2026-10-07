package com.actionaura.retail.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AccountBalance
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.ErrorOutline
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.actionaura.retail.net.ApiClient
import com.actionaura.retail.net.BalanceCheck
import com.actionaura.retail.net.BalanceSheet
import com.actionaura.retail.net.apiErrorMessage
import com.actionaura.retail.ui.CAP_BOOKS_VIEW
import com.actionaura.retail.ui.RetailSession
import com.actionaura.retail.ui.components.EmptyState
import com.actionaura.retail.ui.components.SectionHeader
import com.actionaura.retail.ui.components.TillCard
import com.actionaura.retail.ui.i18n.money
import com.actionaura.retail.ui.i18n.tr
import com.actionaura.retail.ui.theme.Success
import kotlinx.coroutines.launch
import java.time.LocalDate

// ══════════════════════════════════════════════════════════════════════════════
//  BALANCE SHEET -- retail-hardware-viewports
//
//  core/retail/financial_statements.py::balance_sheet, exposed by GET
//  .../reports/balance-sheet (retail_api.py::report_balance_sheet). Mirrors
//  the desktop's own _renderBalanceSheet (subsystem-retail.js) -- statement
//  order top to bottom (balance check, assets, liabilities, equity), the
//  cost-basis warning shown first, "nothing posted" kept distinct from a
//  real (balanced) zero -- and REUSES its already-vetted Arabic strings
//  verbatim; see ui/i18n/Strings.kt's Balance Sheet section.
//
//  THE ONE STRUCTURAL DIFFERENCE FROM PROFIT & LOSS: this is a SNAPSHOT, not
//  a period. `as_of_date` scopes every underlying query with `business_date
//  <= ?`, never a range -- see balance_sheet's own docstring. So the control
//  here is a single date stepper (the exact shape DailyCashScreen already
//  uses for its own single-day report), never the two-ended range control
//  ProfitAndLossScreen.kt needs for its month. Papering over that
//  difference with one shared control would hide a real distinction the
//  task this screen was built for is explicit about.
//
//  THE BALANCE CHECK is the one figure this whole screen exists to answer,
//  shown PLAINLY, first thing under the date control -- green when
//  `assets_total` and `liabilities_and_equity_total` agree, LOUD (red, with
//  the signed difference) when they do not. `balance_sheet`'s own docstring
//  calls this "provably" true for a healthy ledger, which is exactly the
//  class of claim this codebase's engineering standard says must be
//  MEASURED, never merely asserted -- so it is read from the server's own
//  `balance_check.is_balanced`, never recomputed here.
//
//  [StatementActivity]/[incomeStatementActivity]/[CostBasisWarning]/
//  [costBasisWarning]/[CostBasisWarningCard]/[LabelledAmountRow]/
//  [LedgerAmountRow] are declared in ProfitAndLossScreen.kt (same package)
//  and reused here verbatim -- both statements carry the identical honesty
//  flag and the identical row shapes, and BOTH screens must never render a
//  false answer for either.
// ══════════════════════════════════════════════════════════════════════════════

internal fun balanceSheetActivity(bs: BalanceSheet): StatementActivity =
    if (bs.has_activity) StatementActivity.HAS_ACTIVITY else StatementActivity.NO_ACTIVITY

internal fun costBasisWarning(bs: BalanceSheet): CostBasisWarning =
    CostBasisWarning(visible = bs.cost_basis_incomplete, affectedSaleCount = bs.cost_basis_incomplete_sale_count)

/**
 * One side of `balance_check`'s verdict, read honestly. [UNKNOWN] is the
 * state a bare `is_balanced: Boolean = false` default would erase: a
 * malformed/absent `balance_check` (the CONTAINER, not any one of its
 * fields -- see [BalanceCheck]'s doc comment on [BalanceSheet.balance_check])
 * must never render as either a false "Balanced" or a false "Out of
 * Balance" -- both are alarms/assurances the server never actually gave.
 * Same shape as TrialBalanceScreen.kt's [TieState]/`arTieState`.
 */
internal enum class BalanceCheckState { BALANCED, DOES_NOT_BALANCE, UNKNOWN }

internal fun balanceCheckState(check: BalanceCheck?): BalanceCheckState = when (check?.is_balanced) {
    true -> BalanceCheckState.BALANCED
    false -> BalanceCheckState.DOES_NOT_BALANCE
    null -> BalanceCheckState.UNKNOWN
}

@Composable
fun BalanceSheetScreen(snackbar: SnackbarHostState) {
    // Capability gate FIRST, before any fetch is scheduled -- same shape as
    // TrialBalanceScreen's identical gate; see that composable's doc comment
    // for the full reasoning.
    if (!RetailSession.hasCapability(CAP_BOOKS_VIEW)) {
        EmptyState(
            Icons.Default.Shield,
            tr("Reports access required"),
            tr("Only accounts with reports access can see the balance sheet. " +
                "Ask the owner to grant it."),
        )
        return
    }

    // A snapshot as-of TODAY by default -- matches the desktop's own
    // "a balance sheet is conventionally read 'as things stand right now'"
    // default, and reuses DailyCashScreen's exact single-date Prev/Next
    // stepper shape (LocalDate, ISO string sent verbatim as the query param).
    var asOfDate by remember { mutableStateOf(LocalDate.now()) }
    var data by remember { mutableStateOf<BalanceSheet?>(null) }
    var loading by remember { mutableStateOf(true) }
    var loadError by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    suspend fun load(date: LocalDate) {
        try {
            // `?: throw`, not `?: BalanceSheet()` -- identical Gson explicit-
            // null reasoning ProfitAndLossScreen's own load() carries.
            data = ApiClient.get().balanceSheet(date.toString()).data
                ?: throw IllegalStateException("Malformed response: balance sheet data was null")
            loadError = null
        } catch (e: Exception) {
            loadError = apiErrorMessage(e)
            data = null
        }
    }
    LaunchedEffect(asOfDate) {
        loading = true
        load(asOfDate)
        loading = false
    }

    Column(Modifier.fillMaxSize()) {
        // Always visible through loading/error/empty, matching
        // ProfitAndLossScreen's identical reasoning for its own range
        // control.
        Row(Modifier.fillMaxWidth().padding(16.dp, 16.dp, 16.dp, 0.dp), verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = { asOfDate = asOfDate.minusDays(1) }) { Text(tr("‹ Prev")) }
            Text("${tr("As of Date")}: $asOfDate", Modifier.weight(1f), textAlign = TextAlign.Center,
                style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            TextButton(onClick = { if (asOfDate.isBefore(LocalDate.now())) asOfDate = asOfDate.plusDays(1) }) {
                Text(tr("Next ›"))
            }
        }

        val current = data
        if (loading) {
            Box(Modifier.fillMaxWidth().padding(40.dp), contentAlignment = Alignment.Center) {
                CircularProgressIndicator()
            }
        } else if (loadError != null) {
            EmptyState(
                Icons.Default.AccountBalance, tr("Couldn't load the balance sheet"), loadError!!,
                ctaText = tr("Try again"),
                onCta = { scope.launch { loading = true; load(asOfDate); loading = false } },
            )
        } else if (current == null) {
            // Defensive only -- the `?: throw` above means a completed load
            // with no error always leaves `data` non-null.
            EmptyState(
                Icons.Default.AccountBalance, tr("Couldn't load the balance sheet"),
                tr("Something went wrong loading this report."),
            )
        } else {
            BalanceSheetBody(current)
        }
    }
}

@Composable
private fun BalanceSheetBody(bs: BalanceSheet) {
    when (balanceSheetActivity(bs)) {
        StatementActivity.NO_ACTIVITY -> EmptyState(
            Icons.Default.AccountBalance, tr("Nothing has been posted as of this date"),
            tr("No sale, return, purchase or expense was recorded on or before this date. " +
                "This is not a zero balance sheet -- there is nothing to measure yet."),
        )
        StatementActivity.HAS_ACTIVITY -> Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            CostBasisWarningCard(
                costBasisWarning(bs),
                tr("Cost basis is incomplete"),
                tr("These sales have no recorded cost of goods. Inventory and retained earnings " +
                    "shown below are overstated."),
            )

            BalanceCheckCard(bs.balance_check)

            SectionHeader(tr("Assets"))
            if (bs.assets.accounts.isEmpty()) {
                Text(tr("No asset accounts recorded as of this date."),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(vertical = 4.dp))
            } else {
                TillCard(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp)) {
                        bs.assets.accounts.forEach { a -> LedgerAmountRow(a) }
                        HorizontalDivider(Modifier.padding(vertical = 6.dp))
                        LabelledAmountRow(tr("Total"), money(bs.assets.total), bold = true)
                    }
                }
            }

            SectionHeader(tr("Liabilities"))
            if (bs.liabilities.accounts.isEmpty()) {
                Text(tr("No liability accounts recorded as of this date."),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(vertical = 4.dp))
            } else {
                TillCard(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp)) {
                        bs.liabilities.accounts.forEach { a -> LedgerAmountRow(a) }
                        HorizontalDivider(Modifier.padding(vertical = 6.dp))
                        LabelledAmountRow(tr("Total"), money(bs.liabilities.total), bold = true)
                    }
                }
            }

            SectionHeader(tr("Equity"))
            if (bs.equity.accounts.isEmpty()) {
                Text(tr("No equity accounts recorded as of this date."),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(vertical = 4.dp))
            }
            TillCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp)) {
                    bs.equity.accounts.forEach { a -> LedgerAmountRow(a) }
                    if (bs.equity.accounts.isNotEmpty()) {
                        HorizontalDivider(Modifier.padding(vertical = 6.dp))
                        LabelledAmountRow(tr("Total"), money(bs.equity.owners_equity), bold = true)
                    }
                    Spacer(Modifier.height(6.dp))
                    LabelledAmountRow(tr("Retained Earnings"), money(bs.equity.retained_earnings))
                    Text(
                        tr("Accumulated profit since the ledger began. This product has no period-close " +
                            "step, so this figure is always current rather than a posted ledger entry."),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    HorizontalDivider(Modifier.padding(vertical = 8.dp))
                    LabelledAmountRow(tr("Total Equity"), money(bs.equity.total), bold = true)
                }
            }

            // The closing bottom bar -- visually the answer, matching
            // ProfitAndLossScreen's Net Profit card. Not itself pass/fail
            // coloured: the balance verdict already lives in the card above.
            TillCard(Modifier.fillMaxWidth(), accent = MaterialTheme.colorScheme.primary) {
                Row(Modifier.padding(18.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(tr("Total Liabilities and Equity"), Modifier.weight(1f),
                        style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                    Text(money(bs.total_liabilities_and_equity), style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.ExtraBold, color = MaterialTheme.colorScheme.primary)
                }
            }
        }
    }
}

/**
 * The one figure this whole screen exists to surface -- shown PLAINLY,
 * first thing under the date control (matches
 * `_renderBalanceSheet`/`_loadTrialBalance`'s identical placement). Green
 * when [BalanceCheck.is_balanced], LOUD (red, with the signed difference)
 * when it does not, "couldn't verify" (never a guessed verdict) when the
 * container itself is missing/malformed.
 */
@Composable
private fun BalanceCheckCard(check: BalanceCheck?) {
    val state = balanceCheckState(check)
    val accent: Color? = when (state) {
        BalanceCheckState.BALANCED -> Success
        BalanceCheckState.DOES_NOT_BALANCE -> MaterialTheme.colorScheme.error
        BalanceCheckState.UNKNOWN -> null
    }
    val badgeLabel = when (state) {
        BalanceCheckState.BALANCED -> tr("Balanced")
        BalanceCheckState.DOES_NOT_BALANCE -> tr("Out of Balance")
        BalanceCheckState.UNKNOWN -> tr("Couldn't verify")
    }
    val badgeColor = when (state) {
        BalanceCheckState.BALANCED -> Success
        BalanceCheckState.DOES_NOT_BALANCE -> MaterialTheme.colorScheme.error
        BalanceCheckState.UNKNOWN -> MaterialTheme.colorScheme.onSurfaceVariant
    }
    val badgeIcon: ImageVector = when (state) {
        BalanceCheckState.BALANCED -> Icons.Default.CheckCircle
        BalanceCheckState.DOES_NOT_BALANCE -> Icons.Default.ErrorOutline
        BalanceCheckState.UNKNOWN -> Icons.Default.Info
    }
    TillCard(Modifier.fillMaxWidth(), accent = accent) {
        Column(Modifier.padding(18.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(24.dp)) {
                Column(Modifier.weight(1f)) {
                    Text(tr("Total Assets"), style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(check?.assets_total?.let { money(it) } ?: "—",
                        style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                }
                Column(Modifier.weight(1f)) {
                    Text(tr("Total Liabilities and Equity"), style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(check?.liabilities_and_equity_total?.let { money(it) } ?: "—",
                        style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                }
            }
            Spacer(Modifier.height(12.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(badgeIcon, null, tint = badgeColor, modifier = Modifier.height(18.dp).width(18.dp))
                Spacer(Modifier.width(6.dp))
                Text(badgeLabel, color = badgeColor, fontWeight = FontWeight.Bold)
            }
            if (state == BalanceCheckState.DOES_NOT_BALANCE) {
                Spacer(Modifier.height(6.dp))
                Text(tr("Difference") + ": " + (check?.difference?.let { money(it) } ?: "—"),
                    color = MaterialTheme.colorScheme.error, fontWeight = FontWeight.Bold)
            }
        }
    }
}
