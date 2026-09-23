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
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.automirrored.filled.TrendingUp
import androidx.compose.material.icons.filled.WarningAmber
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.actionaura.retail.net.ApiClient
import com.actionaura.retail.net.IncomeStatement
import com.actionaura.retail.net.LedgerAmount
import com.actionaura.retail.net.apiErrorMessage
import com.actionaura.retail.ui.CAP_REPORTS
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
//  PROFIT & LOSS (income statement) -- retail-hardware-viewports
//
//  core/retail/financial_statements.py::income_statement has been correct
//  since it shipped alongside the general ledger, but GET .../reports/
//  profit-and-loss (retail_api.py::report_profit_and_loss) is the only route
//  that exposes it, and until this screen the ONLY client reading it was the
//  desktop's own _renderProfitAndLoss (subsystem-retail.js). This screen
//  deliberately mirrors that shape (month-to-date default, the cost-basis
//  warning shown first and prominently, "nothing posted" kept distinct from
//  a real zero, statement order top to bottom the way an accountant reads
//  one) and REUSES its already-vetted Arabic strings verbatim -- see
//  ui/i18n/Strings.kt's Profit & Loss section for the pairing.
//
//  THE ONE FLAG THIS SCREEN MUST NEVER BURY: `cost_basis_incomplete`. TRUE
//  means the period contains pre-v39 sales with no cost snapshot, so
//  `cost_of_goods_sold` is UNDERSTATED and `gross_profit`/`net_profit` are
//  therefore OVERSTATED -- see income_statement's own module docstring,
//  point 5. Shown as its own card, first thing under the period controls,
//  never folded into a footnote under the numbers it puts in doubt.
//
//  Same "three states, not two" instinct TrialBalanceScreen.kt established:
//  [StatementActivity] keeps "nothing posted this period" distinct from a
//  genuinely-computed zero, the identical reason [TrialBalanceStatus]
//  distinguishes NOTHING_POSTED from BALANCED. That enum, and
//  [CostBasisWarning]/[costBasisWarning], are declared here and reused
//  verbatim by BalanceSheetScreen.kt (same package, both statements carry
//  the identical honesty flag).
// ══════════════════════════════════════════════════════════════════════════════

/**
 * Whether ANYTHING moved in this statement's scope -- read from the server's
 * own `has_activity` (a Gson-protected `Boolean` primitive, so it cannot be
 * silently null-overwritten the way an object field can; see
 * [IncomeStatement]'s doc comment). [NO_ACTIVITY] must render as an empty
 * state, never as a fully-drawn statement whose every figure happens to be
 * zero -- collapsing the two is the same lie [TrialBalanceStatus.NOTHING_
 * POSTED] exists to refuse for the trial balance.
 */
internal enum class StatementActivity { HAS_ACTIVITY, NO_ACTIVITY }

internal fun incomeStatementActivity(ps: IncomeStatement): StatementActivity =
    if (ps.has_activity) StatementActivity.HAS_ACTIVITY else StatementActivity.NO_ACTIVITY

/**
 * The honesty flag, read once into a small testable shape rather than an
 * inline `if` at the render site -- [visible] must be `false` whenever
 * [IncomeStatement.cost_basis_incomplete] (or its balance-sheet twin) is
 * `false`, and the warning card below must never render when it is.
 */
internal data class CostBasisWarning(val visible: Boolean, val affectedSaleCount: Int)

internal fun costBasisWarning(ps: IncomeStatement): CostBasisWarning =
    CostBasisWarning(visible = ps.cost_basis_incomplete, affectedSaleCount = ps.cost_basis_incomplete_sale_count)

/**
 * Month-to-date range for the calendar month starting at [monthStart] (must
 * be the 1st of a month), clamped so it never extends past [today] --
 * matches the desktop's own `_renderProfitAndLoss` default exactly ("the 1st
 * of the current calendar month through today... a calendar boundary is
 * what makes one P&L comparable to the same shop's own prior month, which a
 * trailing 30-day window is not"). A past month (fully clamped by its own
 * last day, never by [today]) returns its full range; the current month
 * returns `[monthStart, today]`.
 */
internal fun monthToDateRange(monthStart: LocalDate, today: LocalDate): Pair<LocalDate, LocalDate> {
    val monthEnd = monthStart.plusMonths(1).minusDays(1)
    val end = if (monthEnd.isAfter(today)) today else monthEnd
    return monthStart to end
}

/** `gross_margin` is a RATIO (0.4, not 40.0) and nullable -- see
 *  [IncomeStatement.gross_margin]'s doc comment. `null` reads as "—", never
 *  a misleading 0%. */
internal fun grossMarginPercentLabel(ratio: Double?): String {
    if (ratio == null) return "—"
    val pct = kotlin.math.round(ratio * 1000.0) / 10.0 // one decimal, locale-free
    return "$pct%"
}

@Composable
fun ProfitAndLossScreen(snackbar: SnackbarHostState) {
    // Capability gate FIRST, before any fetch is scheduled -- same shape as
    // TrialBalanceScreen's identical gate; see that composable's doc comment
    // for the full reasoning.
    if (!RetailSession.hasCapability(CAP_REPORTS)) {
        EmptyState(
            Icons.Default.Shield,
            tr("Reports access required"),
            tr("Only accounts with reports access can see the profit and loss statement. " +
                "Ask the owner to grant it."),
        )
        return
    }

    var monthStart by remember { mutableStateOf(LocalDate.now().withDayOfMonth(1)) }
    var data by remember { mutableStateOf<IncomeStatement?>(null) }
    var loading by remember { mutableStateOf(true) }
    var loadError by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    suspend fun load(start: LocalDate, end: LocalDate) {
        try {
            // `?: throw`, not `?: IncomeStatement()` -- same Gson explicit-
            // null reasoning TrialBalanceScreen's own load() carries: Gson
            // writes an explicit JSON null straight into `data` regardless
            // of the Kotlin type, and defaulting it here would render a
            // malformed 200 as a freshly-zeroed, "nothing posted" period --
            // the exact "zeros implying a real answer" lie this screen must
            // never tell, arriving from a different direction.
            data = ApiClient.get().profitAndLoss(start.toString(), end.toString()).data
                ?: throw IllegalStateException("Malformed response: profit and loss data was null")
            loadError = null
        } catch (e: Exception) {
            loadError = apiErrorMessage(e)
            data = null
        }
    }
    LaunchedEffect(monthStart) {
        loading = true
        val (start, end) = monthToDateRange(monthStart, LocalDate.now())
        load(start, end)
        loading = false
    }

    Column(Modifier.fillMaxSize()) {
        // The period control stays visible through loading/error/empty --
        // matches DailyCashScreen's identical Prev/Next-always-on-screen
        // shape, so a failed or empty period is still navigable away from
        // without leaving the screen.
        val (rangeStart, rangeEnd) = monthToDateRange(monthStart, LocalDate.now())
        Row(Modifier.fillMaxWidth().padding(16.dp, 16.dp, 16.dp, 0.dp), verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = { monthStart = monthStart.minusMonths(1) }) { Text(tr("‹ Prev")) }
            Text("$rangeStart – $rangeEnd", Modifier.weight(1f), textAlign = TextAlign.Center,
                style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            TextButton(onClick = {
                val thisMonth = LocalDate.now().withDayOfMonth(1)
                if (monthStart.isBefore(thisMonth)) monthStart = monthStart.plusMonths(1)
            }) { Text(tr("Next ›")) }
        }

        val current = data
        if (loading) {
            Box(Modifier.fillMaxWidth().padding(40.dp), contentAlignment = Alignment.Center) {
                CircularProgressIndicator()
            }
        } else if (loadError != null) {
            EmptyState(
                Icons.AutoMirrored.Filled.TrendingUp, tr("Couldn't load the profit and loss statement"), loadError!!,
                ctaText = tr("Try again"),
                onCta = { scope.launch { loading = true; load(rangeStart, rangeEnd); loading = false } },
            )
        } else if (current == null) {
            // Defensive only -- the `?: throw` above means a completed load
            // with no error always leaves `data` non-null.
            EmptyState(
                Icons.AutoMirrored.Filled.TrendingUp, tr("Couldn't load the profit and loss statement"),
                tr("Something went wrong loading this report."),
            )
        } else {
            ProfitAndLossBody(current)
        }
    }
}

@Composable
private fun ProfitAndLossBody(ps: IncomeStatement) {
    when (incomeStatementActivity(ps)) {
        StatementActivity.NO_ACTIVITY -> EmptyState(
            Icons.AutoMirrored.Filled.TrendingUp, tr("Nothing was posted in this period"),
            tr("No sale, return, purchase or expense was recorded in this date range. " +
                "This is not a zero profit -- there is nothing to measure yet."),
        )
        StatementActivity.HAS_ACTIVITY -> Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            CostBasisWarningCard(
                costBasisWarning(ps),
                tr("Cost basis is incomplete for this period"),
                tr("These sales have no recorded cost of goods. Cost of goods sold is understated, " +
                    "and the gross profit and net profit shown below are overstated."),
            )

            TillCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(18.dp)) {
                    Text(tr("Revenue"), style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(money(ps.revenue), style = MaterialTheme.typography.headlineSmall,
                        fontWeight = FontWeight.ExtraBold)
                    Spacer(Modifier.height(10.dp))
                    LabelledAmountRow(tr("Gross Revenue"), money(ps.gross_revenue))
                    LabelledAmountRow(tr("Sales Returns"), "-" + money(ps.sales_returns))
                }
            }

            TillCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(18.dp)) {
                    LabelledAmountRow(tr("Cost of Goods Sold"), "-" + money(ps.cost_of_goods_sold))
                    HorizontalDivider(Modifier.padding(vertical = 8.dp))
                    LabelledAmountRow(tr("Gross Profit"), money(ps.gross_profit), bold = true)
                    if (ps.gross_margin != null) {
                        LabelledAmountRow(tr("Gross Margin"), grossMarginPercentLabel(ps.gross_margin))
                    }
                }
            }

            SectionHeader(tr("Operating Expenses"))
            if (ps.operating_expenses.accounts.isEmpty()) {
                Text(tr("No operating expenses recorded in this period."),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(vertical = 4.dp))
            } else {
                TillCard(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp)) {
                        ps.operating_expenses.accounts.forEach { a -> LedgerAmountRow(a) }
                        HorizontalDivider(Modifier.padding(vertical = 6.dp))
                        LabelledAmountRow(tr("Total"), money(ps.operating_expenses.total), bold = true)
                    }
                }
            }

            // The bottom line, visually the answer -- accent colour carries
            // the profit/loss verdict, matching TrialBalanceStatusCard's own
            // accent-as-verdict convention.
            val netAccent = if (ps.net_profit >= 0.0) Success else MaterialTheme.colorScheme.error
            TillCard(Modifier.fillMaxWidth(), accent = netAccent) {
                Column(Modifier.padding(18.dp)) {
                    Text(tr("Net Profit"), style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(money(ps.net_profit), style = MaterialTheme.typography.headlineMedium,
                        fontWeight = FontWeight.ExtraBold, color = netAccent)
                }
            }
        }
    }
}

/** The cost-basis honesty card both statements share -- [warning]'s
 *  [CostBasisWarning.visible] gates it entirely (a `null`/invisible warning
 *  renders nothing, not an empty card). `internal` so BalanceSheetScreen.kt
 *  (same package) can reuse it with its own title/body text -- financial_
 *  statements.py gives the two statements DIFFERENT explanations of the same
 *  root cause (COGS/gross&net profit here, Inventory/retained earnings
 *  there), so the text is a parameter, never hardcoded to one screen. */
@Composable
internal fun CostBasisWarningCard(warning: CostBasisWarning, title: String, body: String) {
    if (!warning.visible) return
    TillCard(Modifier.fillMaxWidth(), accent = MaterialTheme.colorScheme.error) {
        Column(Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Default.WarningAmber, null, tint = MaterialTheme.colorScheme.error)
                Spacer(Modifier.width(8.dp))
                Text(title, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.error)
            }
            Spacer(Modifier.height(8.dp))
            Row(verticalAlignment = Alignment.Bottom) {
                Text("${warning.affectedSaleCount}", style = MaterialTheme.typography.headlineSmall,
                    fontWeight = FontWeight.ExtraBold, color = MaterialTheme.colorScheme.error)
                Spacer(Modifier.width(8.dp))
                Text(tr("sale(s) affected"), color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Spacer(Modifier.height(8.dp))
            Text(body, style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/** A plain label/amount row -- the shared shape for every summary line in
 *  both statements below (Gross Revenue, Sales Returns, Cost of Goods Sold,
 *  Gross Profit, Gross Margin, Net Profit, and every Total row). `internal`
 *  for the same cross-file reuse reason [CostBasisWarningCard] is. */
@Composable
internal fun LabelledAmountRow(label: String, value: String, bold: Boolean = false) {
    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(label, Modifier.weight(1f),
            color = if (bold) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant,
            fontWeight = if (bold) FontWeight.Bold else FontWeight.Normal)
        Text(value, fontWeight = if (bold) FontWeight.ExtraBold else FontWeight.Medium)
    }
}

/** One `LedgerAmount` row (an operating-expense account, or a balance-sheet
 *  asset/liability/equity account) -- code bidi-isolated, name left
 *  UNTRANSLATED (server data, matching [GlAccountRow]'s identical
 *  convention: "account names are server data, not a UI string this client
 *  owns the wording of"). `internal` for the same reuse reason as above. */
@Composable
internal fun LedgerAmountRow(a: LedgerAmount) {
    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        Row(Modifier.weight(1f), verticalAlignment = Alignment.CenterVertically) {
            Text(bidiIsolate(a.code.orEmpty()), style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.width(8.dp))
            Text(a.name.orEmpty(), maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        Text(money(a.amount), fontWeight = FontWeight.Medium)
    }
}
