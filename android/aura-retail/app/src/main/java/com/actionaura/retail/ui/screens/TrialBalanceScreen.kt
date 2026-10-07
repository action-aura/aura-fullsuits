package com.actionaura.retail.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AccountBalance
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.ErrorOutline
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
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
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.actionaura.retail.net.ApiClient
import com.actionaura.retail.net.GlAccount
import com.actionaura.retail.net.SubledgerTie
import com.actionaura.retail.net.TrialBalance
import com.actionaura.retail.net.apiErrorMessage
import com.actionaura.retail.ui.CAP_BOOKS_VIEW
import com.actionaura.retail.ui.RetailSession
import com.actionaura.retail.ui.components.EmptyState
import com.actionaura.retail.ui.components.SectionHeader
import com.actionaura.retail.ui.components.SkeletonList
import com.actionaura.retail.ui.components.TillCard
import com.actionaura.retail.ui.i18n.money
import com.actionaura.retail.ui.i18n.tr
import com.actionaura.retail.ui.theme.Info
import com.actionaura.retail.ui.theme.Success
import kotlinx.coroutines.launch

// ══════════════════════════════════════════════════════════════════════════════
//  TRIAL BALANCE — retail-hardware-viewports, schema v38/v39 general ledger
//
//  core/retail/general_ledger.py has posted every sale/return/payment/purchase
//  order to gl_journal_lines since v38 shipped; GET .../reports/trial-balance
//  (retail_api.py::report_trial_balance) is the one route that exposes it, and
//  this is the Android half of making it visible -- the desktop half is
//  subsystem-retail.js's `_renderTrialBalance`/`_loadTrialBalance`, whose shape
//  (one status card, one account table, one visible subledger-tie block) this
//  screen deliberately mirrors so an owner switching between the till and the
//  desktop sees the same report, not two different ones answering the same
//  question.
//
//  THE POINT OF THIS SCREEN is not the bare account balances -- per report_
//  trial_balance's own docstring, `subledger` is "the reason the route exists,
//  not a bonus field alongside it". A GL that balances against its own
//  arithmetic but has drifted from the customers/suppliers subledger the till
//  actually collects/pays against is exactly as broken as one that does not
//  balance at all, just quieter, because nothing about the GL's own
//  debit/credit arithmetic would ever show it. So the tie-back is its own
//  visible section here too, never a footnote under the table.
// ══════════════════════════════════════════════════════════════════════════════

/**
 * The one honest reading of `is_balanced` + the two totals it is computed
 * from. Three states, not two, because collapsing [NOTHING_POSTED] into
 * [BALANCED] tells the owner something untrue.
 *
 *  - [BALANCED]        total debits == total credits, and something has
 *                       actually posted. The GL agrees with itself.
 *  - [OUT_OF_BALANCE]  they disagree -- the double-entry posting itself is
 *                       broken, not a display glitch, and needs attention.
 *  - [NOTHING_POSTED]  both totals are exactly zero. `ensure_default_chart`
 *                       seeds the chart of accounts for every company, so a
 *                       brand-new shop's `accounts` list is never empty --
 *                       it is ten rows, all zeroed -- and 0.0 == 0.0 makes
 *                       `is_balanced` trivially true. Showing that as
 *                       "Balanced" would claim the books were audited and
 *                       agree, when the true fact is that nothing has been
 *                       rung up yet. A seeded, zeroed chart is not a
 *                       reconciled one.
 */
internal enum class TrialBalanceStatus { BALANCED, OUT_OF_BALANCE, NOTHING_POSTED }

internal fun trialBalanceStatus(tb: TrialBalance): TrialBalanceStatus = when {
    tb.total_debit == 0.0 && tb.total_credit == 0.0 -> TrialBalanceStatus.NOTHING_POSTED
    tb.is_balanced -> TrialBalanceStatus.BALANCED
    else -> TrialBalanceStatus.OUT_OF_BALANCE
}

/**
 * One side (AR or AP) of `gl.subledger_tie`'s verdict, read honestly.
 *
 * [UNKNOWN] is the state the desktop does not have: `subsystem-retail.js`
 * defaults a missing `subledger` to `{}` and then reads `sl.ar_ties` off it,
 * so an absent/malformed subledger there renders the same red "Does Not Tie"
 * badge as a real mismatch would -- an alarm the server never actually
 * raised, over data it never sent. This client keeps the two apart: a null
 * [TrialBalance.subledger] (the Gson-explicit-null hazard [TrialBalance]'s
 * own doc comment names) answers [UNKNOWN], never [DOES_NOT_TIE].
 */
internal enum class TieState { TIES, DOES_NOT_TIE, UNKNOWN }

internal fun arTieState(subledger: SubledgerTie?): TieState = when (subledger?.ar_ties) {
    true -> TieState.TIES
    false -> TieState.DOES_NOT_TIE
    null -> TieState.UNKNOWN
}

internal fun apTieState(subledger: SubledgerTie?): TieState = when (subledger?.ap_ties) {
    true -> TieState.TIES
    false -> TieState.DOES_NOT_TIE
    null -> TieState.UNKNOWN
}

/**
 * `account_type` -> an ENGLISH catalogue key, translated at the render site
 * (matches this file's module-wide convention -- see e.g.
 * EmployeeSalesScreen.kt's `BY_EMPLOYEE_UNAVAILABLE`).
 *
 * `DEFAULT_CHART` (general_ledger.py) seeds exactly five types: asset,
 * liability, equity, revenue, expense. The desktop's own
 * `_TRIAL_BALANCE_TYPE_LABEL` map (subsystem-retail.js) keys `'income'`
 * instead of `'revenue'` -- so on the desktop today, every revenue account
 * (4000 Sales Revenue, 4900 Sales Returns and Allowances) falls through to
 * that map's `'Other'` fallback rather than showing a real label. That is a
 * pre-existing desktop defect, not one to inherit here: `'revenue'` is mapped
 * to the SAME translated word the desktop uses for the concept
 * ("Income"/"إيراد", already in the catalogue for that reason) rather than to
 * the desktop's dead `'income'` key or to a new, unreviewed Arabic string.
 */
internal fun accountTypeLabel(type: String?): String = when (type) {
    "asset" -> "Asset"
    "liability" -> "Liability"
    "equity" -> "Equity"
    "revenue" -> "Income"
    "expense" -> "Expense"
    else -> "Other"
}

@Composable
fun TrialBalanceScreen(snackbar: SnackbarHostState) {
    // Capability gate FIRST, before any fetch is scheduled -- same "degrade
    // honestly, check before fetching" shape as EmployeeSalesScreen's
    // identical gate (see that composable's doc comment for the full
    // reasoning). Rendering advice only: `hasCapability` fails open until the
    // session resolves, and the route re-checks server-side regardless.
    if (!RetailSession.hasCapability(CAP_BOOKS_VIEW)) {
        EmptyState(
            Icons.Default.Shield,
            tr("Reports access required"),
            tr("Only accounts with reports access can see the trial balance. " +
                "Ask the owner to grant it."),
        )
        return
    }

    var data by remember { mutableStateOf<TrialBalance?>(null) }
    var loading by remember { mutableStateOf(true) }
    // Distinct from an empty/zeroed ledger: a load that FAILED and a ledger
    // with nothing posted are different facts, and collapsing them is how a
    // screen tells an owner their books are fine when their session expired.
    var loadError by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    suspend fun load() {
        try {
            // `?: throw`, not `?: TrialBalance()`. Gson writes an explicit
            // JSON null straight into `data` regardless of what the Kotlin
            // type claims (see TrialBalanceResponse's doc comment); defaulting
            // it here would trade a crash for a screen that reports a
            // malformed 200 as a freshly-seeded, all-zero ledger -- exactly
            // the "zeros implying balanced books" lie this screen exists to
            // avoid, arriving from a different direction. Throwing puts a
            // malformed body on the same path as every other failure.
            data = ApiClient.get().trialBalance().data
                ?: throw IllegalStateException("Malformed response: trial balance data was null")
            loadError = null
        } catch (e: Exception) {
            loadError = apiErrorMessage(e)
            data = null
        }
    }
    LaunchedEffect(Unit) { loading = true; load(); loading = false }

    val current = data
    when {
        loading -> SkeletonList()
        loadError != null -> EmptyState(
            Icons.Default.AccountBalance, tr("Couldn't load the trial balance"), loadError!!,
            ctaText = tr("Try again"),
            onCta = { scope.launch { loading = true; load(); loading = false } },
        )
        // Defensive only -- the `?: throw` above means a completed load with
        // no error always leaves `data` non-null. Kept so this `when` has no
        // silent fallthrough rather than because this branch is reachable.
        current == null -> EmptyState(
            Icons.Default.AccountBalance, tr("Couldn't load the trial balance"),
            tr("Something went wrong loading this report."),
        )
        else -> TrialBalanceBody(current)
    }
}

@Composable
private fun TrialBalanceBody(tb: TrialBalance) {
    val status = trialBalanceStatus(tb)
    LazyColumn(
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        item { TrialBalanceStatusCard(tb, status) }

        item { SectionHeader(tr("Subledger Reconciliation")) }
        item {
            SubledgerTieCard(
                heading = tr("GL Accounts Receivable"),
                gl = tb.subledger?.ar_gl,
                subLabel = tr("Customer Balances (Subledger)"),
                sub = tb.subledger?.ar_subledger,
                state = arTieState(tb.subledger),
            )
        }
        item {
            SubledgerTieCard(
                heading = tr("GL Accounts Payable"),
                gl = tb.subledger?.ap_gl,
                subLabel = tr("Supplier Balances (Subledger)"),
                sub = tb.subledger?.ap_subledger,
                state = apTieState(tb.subledger),
            )
        }

        item { SectionHeader(tr("Accounts")) }
        if (tb.accounts.isEmpty()) {
            // Practically unreachable (ensure_default_chart always seeds ten
            // rows), but the desktop draws this exact defensive state, and an
            // empty list here must never read as "the ledger has nothing in
            // it, everything is fine" -- see _renderTrialBalance's own
            // identical empty state.
            item {
                EmptyState(
                    Icons.Default.AccountBalance, tr("No accounts posted yet"),
                    tr("Transactions will appear here once the general ledger starts recording them."),
                )
            }
        } else {
            items(tb.accounts) { a -> GlAccountRow(a) }
        }
    }
}

@Composable
private fun TrialBalanceStatusCard(tb: TrialBalance, status: TrialBalanceStatus) {
    val accent: Color = when (status) {
        TrialBalanceStatus.BALANCED -> Success
        TrialBalanceStatus.OUT_OF_BALANCE -> MaterialTheme.colorScheme.error
        TrialBalanceStatus.NOTHING_POSTED -> Info
    }
    val icon: ImageVector = when (status) {
        TrialBalanceStatus.BALANCED -> Icons.Default.CheckCircle
        TrialBalanceStatus.OUT_OF_BALANCE -> Icons.Default.ErrorOutline
        TrialBalanceStatus.NOTHING_POSTED -> Icons.Default.Info
    }
    val label = when (status) {
        TrialBalanceStatus.BALANCED -> tr("Balanced")
        TrialBalanceStatus.OUT_OF_BALANCE -> tr("Out of Balance")
        TrialBalanceStatus.NOTHING_POSTED -> tr("Nothing posted yet")
    }
    val explanation = when (status) {
        TrialBalanceStatus.BALANCED -> tr("Total debits equal total credits.")
        TrialBalanceStatus.OUT_OF_BALANCE ->
            tr("Total debits and total credits do not match. This needs attention.")
        TrialBalanceStatus.NOTHING_POSTED ->
            tr("No sales, purchases or payments have posted to the ledger yet.")
    }
    TillCard(Modifier.fillMaxWidth(), accent = accent) {
        Column(Modifier.padding(18.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(icon, null, tint = accent)
                Spacer(Modifier.width(10.dp))
                Text(label, style = MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.ExtraBold, color = accent)
            }
            Spacer(Modifier.height(6.dp))
            Text(explanation, style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(14.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(28.dp)) {
                Column {
                    Text(tr("Debit Amount"), style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(money(tb.total_debit), style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold)
                }
                Column {
                    Text(tr("Credit Amount"), style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(money(tb.total_credit), style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold)
                }
            }
        }
    }
}

@Composable
private fun SubledgerTieCard(heading: String, gl: Double?, subLabel: String, sub: Double?, state: TieState) {
    val accent: Color? = when (state) {
        TieState.TIES -> null
        TieState.DOES_NOT_TIE -> MaterialTheme.colorScheme.error
        TieState.UNKNOWN -> null
    }
    val badgeLabel = when (state) {
        TieState.TIES -> tr("Ties")
        TieState.DOES_NOT_TIE -> tr("Does Not Tie")
        TieState.UNKNOWN -> tr("Couldn't verify")
    }
    val badgeColor = when (state) {
        TieState.TIES -> Success
        TieState.DOES_NOT_TIE -> MaterialTheme.colorScheme.error
        TieState.UNKNOWN -> MaterialTheme.colorScheme.onSurfaceVariant
    }
    val badgeIcon: ImageVector = when (state) {
        TieState.TIES -> Icons.Default.CheckCircle
        TieState.DOES_NOT_TIE -> Icons.Default.ErrorOutline
        TieState.UNKNOWN -> Icons.Default.Info
    }
    TillCard(Modifier.fillMaxWidth(), accent = accent) {
        Column(Modifier.padding(16.dp)) {
            Text(heading, style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(gl?.let { money(it) } ?: "—", style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(8.dp))
            Text(subLabel, style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(sub?.let { money(it) } ?: "—", style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(10.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(badgeIcon, null, tint = badgeColor, modifier = Modifier.height(16.dp).width(16.dp))
                Spacer(Modifier.width(6.dp))
                Text(badgeLabel, style = MaterialTheme.typography.labelMedium,
                    color = badgeColor, fontWeight = FontWeight.Bold)
            }
        }
    }
}

@Composable
private fun GlAccountRow(a: GlAccount) {
    TillCard(Modifier.fillMaxWidth()) {
        Row(Modifier.padding(16.dp), verticalAlignment = Alignment.Top) {
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    // bidiIsolate (ui/screens/EmployeeSalesScreen.kt, same
                    // package): this line sits in an RTL paragraph on an
                    // Arabic till and the account code is Latin digits --
                    // matches the desktop's own `_bdi(a.code)` treatment.
                    Text(bidiIsolate(a.code.orEmpty()), style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Spacer(Modifier.width(8.dp))
                    // The account NAME is left untranslated, matching the
                    // desktop's own `_esc(a.name)` (never `t(a.name)`) --
                    // account names are server data, not a UI string this
                    // client owns the wording of.
                    Text(a.name.orEmpty(), fontWeight = FontWeight.Bold,
                        maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
                Text(tr(accountTypeLabel(a.account_type)), style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Spacer(Modifier.width(12.dp))
            Column(horizontalAlignment = Alignment.End) {
                Text(money(a.balance), style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.ExtraBold)
                Text(tr("Debit Amount") + " " + money(a.debit), style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text(tr("Credit Amount") + " " + money(a.credit), style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}
