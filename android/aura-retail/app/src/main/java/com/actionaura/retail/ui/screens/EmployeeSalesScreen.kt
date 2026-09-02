@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.actionaura.retail.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
// AutoMirrored, not the plain Filled variant: this app runs RTL in Arabic and
// the mirrored glyph is the one that points the right way there.
import androidx.compose.material.icons.automirrored.filled.HelpOutline
import androidx.compose.material.icons.filled.Groups
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material3.FilterChip
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.actionaura.retail.net.ApiClient
import com.actionaura.retail.net.ByEmployeeResponse
import com.actionaura.retail.net.EmployeeSales
import com.actionaura.retail.net.Sale
import com.actionaura.retail.net.apiErrorMessage
import com.actionaura.retail.ui.CAP_REPORTS
import com.actionaura.retail.ui.RetailSession
import com.actionaura.retail.ui.components.EmptyState
import com.actionaura.retail.ui.components.TillCard
import com.actionaura.retail.ui.components.SkeletonList
import com.actionaura.retail.ui.i18n.money
import com.actionaura.retail.ui.i18n.tr
import com.actionaura.retail.ui.theme.Info
import com.actionaura.retail.ui.theme.Success
import kotlinx.coroutines.launch
import retrofit2.HttpException

// ══════════════════════════════════════════════════════════════════════════════
//  BY EMPLOYEE — takings and transaction count per person, for a chosen period
//
//  The client half of retail schema v13's attribution columns. The desktop
//  Reports page and this screen answer the same question off the same route;
//  what is specific here is that a handset is the device most likely to be
//  looking at a shop whose history predates attribution entirely, so the
//  "nobody was recorded" case is designed for rather than tolerated.
// ══════════════════════════════════════════════════════════════════════════════

/**
 * What this client actually knows about who rang a set of sales. Three states,
 * because collapsing any two of them tells the owner something untrue:
 *
 *  - [NAMED]         the server resolved `actor_user_uid` to a live account.
 *  - [ACCOUNT_GONE]  a uid WAS recorded and no longer resolves. The sale was
 *                    attributed; the person's account has since been removed.
 *                    Folding this into [NOT_RECORDED] would erase a leaver's
 *                    takings from an audit, which is exactly what somebody
 *                    auditing a leaver is looking for.
 *  - [NOT_RECORDED]  no uid at all. On a real install this is the whole of the
 *                    shop's history up to the v13 migration, because v13
 *                    deliberately left `actor_user_uid` NULL rather than
 *                    backfill it: "this device cannot prove it is the terminal
 *                    that rang a sale from before the column existed". A
 *                    client that renders these rows as though somebody had
 *                    been identified throws that refusal away at the last
 *                    step, and a wrong name on a sale is worse than no name.
 */
internal enum class Attribution { NAMED, ACCOUNT_GONE, NOT_RECORDED }

/**
 * The name to print for an attributed row, or null when there is nothing
 * truthful to print.
 *
 * Email first, EMP-000n second, matching EmployeesScreen: the owner manages
 * staff on a screen that shows the email in bold and the assigned id
 * underneath, and a report that ordered them the other way would read as being
 * about different people. Blank strings are not identities -- a server that
 * sends `"email": ""` for a row it could not resolve must not produce a
 * nameless name.
 */
internal fun attributedName(employeeId: String?, email: String?): String? =
    email?.trim()?.takeIf { it.isNotEmpty() }
        ?: employeeId?.trim()?.takeIf { it.isNotEmpty() }

/**
 * Shared classifier for both the report rows and a single sale.
 *
 * The uid is checked FIRST, and that ordering is the whole content of this
 * function. It used to test the name first and never consult `uid` on the
 * [Attribution.NAMED] branch at all, so `(actor_user_uid = null, email =
 * "sam@shop.test")` resolved NAMED. A null uid IS the unattributed aggregate
 * bucket -- every sale rung before v13 added the column, summed into one row --
 * so the moment the route populates an identity field on that row (a "POS"
 * placeholder, a joined-in default, a well-meaning display string), this screen
 * prints a real person's name over sales nobody was recorded for.
 *
 * That is exactly the fabrication the v13 migration refused to commit when it
 * left `actor_user_uid` NULL rather than backfilling it from the free-text
 * `cashier` column -- "a wrong name on a sale is worse than no name" -- arriving
 * at the last step instead of the first. NAMED now requires BOTH: a uid that
 * was actually recorded, AND a name that actually resolved.
 *
 * The three states stay genuinely three: a recorded uid with no resolvable name
 * is [Attribution.ACCOUNT_GONE] (attributed, account since removed), which
 * must not be folded into [Attribution.NOT_RECORDED] or a leaver's takings
 * vanish from the audit somebody is running specifically to find them.
 */
internal fun attributionOf(uid: String?, employeeId: String?, email: String?): Attribution = when {
    uid.isNullOrBlank() -> Attribution.NOT_RECORDED
    attributedName(employeeId, email) != null -> Attribution.NAMED
    else -> Attribution.ACCOUNT_GONE
}

internal fun rowAttribution(row: EmployeeSales): Attribution =
    attributionOf(row.actor_user_uid, row.employee_id, row.email)

internal fun saleAttribution(sale: Sale?): Attribution =
    attributionOf(sale?.actor_user_uid, sale?.actor_employee_id, sale?.actor_email)

/**
 * Wrap a strongly-LTR run (an email, an EMP-000n) so it cannot reorder the
 * Arabic sentence it is embedded in.
 *
 * "نفّذها sam@shop.test" is one paragraph with an RTL base direction and an LTR
 * run inside it. Without an isolate the Unicode bidi algorithm resolves the
 * neutral characters around that run -- a trailing full stop, a separating
 * "·", the digits in EMP-0002 -- against the LTR text, and they visibly jump
 * to the wrong side of the name. On a display-only string the reader has no
 * way to tell that apart from corrupted data.
 *
 * FSI/PDI (U+2068 / U+2069) rather than LRM or an explicit LRE: "first strong
 * isolate" scopes the run WITHOUT asserting a direction for it, so an Arabic
 * employee_id renders right-to-left inside the same wrapper that keeps a Latin
 * email left-to-right. An empty string is returned untouched -- an isolate
 * around nothing is two invisible characters standing where a name should be.
 */
internal fun bidiIsolate(text: String): String =
    if (text.isEmpty()) text else "⁨$text⁩"

/**
 * The one HTTP status on this path that means something more specific than
 * "the server said no", returned as an untranslated catalogue key so this
 * function stays a pure, testable classifier (tr() is applied at the render
 * site, where the active locale lives).
 *
 * `/reports/by-employee` is the newest route in the retail API, and an APK can
 * outrun the backend bundled beside it. The shared mapping in ApiErrors.kt can
 * only render that as "Server error (HTTP 404)" -- true, useless, and
 * indistinguishable from a genuine fault. Everything else deliberately falls
 * through to that shared mapping: a 403 is the capability guard and a 500 is a
 * fault, and dressing either up as "your build is old" would hide a real
 * refusal behind a reassuring sentence.
 */
internal const val BY_EMPLOYEE_UNAVAILABLE =
    "This install's server doesn't provide takings by employee yet."

internal fun missingEndpointKeyOrNull(e: Throwable): String? =
    if (e is HttpException && e.code() == 404) BY_EMPLOYEE_UNAVAILABLE else null

/**
 * What to say when the server refused, worded for a refusal that named no
 * reason. A translated catalogue key, not a bare sentence, because a blank
 * error line is indistinguishable from a screen that failed to render.
 */
internal const val BY_EMPLOYEE_REFUSED = "The server wouldn't send this report."

/**
 * The refusal sentence carried by a 200 whose envelope does not say success, or
 * null when it does.
 *
 * THE BUG: the load path read `reportByEmployee(days).data` and never once
 * consulted the verdict beside it, so a 200 carrying `{"success": false,
 * "error": ..., "data": []}` took the happy path and drew "No sales in this
 * period" -- a refusal rendered as an empty shop, which is the single most
 * misleading thing a takings report can say. Four other screens in this module
 * already check the discriminator; this one deviated from its own module's
 * convention.
 *
 * BOTH spellings are honoured because the route answers with both. `status` is
 * the desktop's discriminator (subsystem-retail.js hard-gates on `body.status
 * !== 'success'`) and wins when present; `success` is what the five sibling
 * chart/KPI report routes emit and what this client's model was built against.
 * Reading only one would turn whichever spelling the backend settled on into a
 * permanent refusal screen. A blank or absent `status` is not a verdict and
 * falls through rather than being read as "not success".
 *
 * The reason is taken from the server verbatim where it sent one -- `message`
 * first, then `error`, the same order and the same two spellings net/
 * ApiErrors.kt already decodes for HTTP failures -- so a capability refusal
 * reads as a capability refusal instead of a generic apology.
 */
internal fun byEmployeeRefusalOrNull(body: ByEmployeeResponse): String? {
    val accepted =
        if (!body.status.isNullOrBlank()) body.status == "success" else body.success
    if (accepted) return null
    return body.message?.trim()?.takeIf { it.isNotEmpty() }
        ?: body.error?.trim()?.takeIf { it.isNotEmpty() }
        ?: BY_EMPLOYEE_REFUSED
}

/**
 * True when a row's takings are NEGATIVE.
 *
 * Not a defect and not a rounding artefact: `metrics.revenue_by_employee`
 * assigns a refund to whoever PROCESSED it rather than to whoever rang the
 * original sale -- the same rule `revenue_by_payment_method` already applies
 * to `refund_method`, and the one the cash drawer reconciles by -- so an
 * employee whose shift was all returns reports a negative figure, and that
 * module's docstring says in as many words that it is correct and must not be
 * "fixed".
 *
 * It does have to be rendered differently. A negative number in the same
 * green as takings reads as money earned when it is money handed back, and
 * that is a misreading an owner makes at a glance rather than on inspection.
 */
internal fun isMoneyOut(revenue: Double): Boolean = revenue < 0.0

@Composable
fun EmployeeSalesScreen(snackbar: SnackbarHostState) {
    // Capability gate FIRST, before any fetch is scheduled -- unlike
    // EmployeesScreen, which loads and then explains. The desktop's own
    // "degrade honestly, check before fetching" pattern (subsystem-retail.js
    // ::_renderDashboard) is the one being matched here: every figure on this
    // screen comes from a single `retail.reports`-gated endpoint, so there is
    // no partial render to fall back to, and firing the request anyway would
    // spend a round trip to be told what the session already knows.
    //
    // This is the honest explanation, never the enforcement:
    // `hasCapability` fails open when the session has not resolved (see
    // holdsCapability), and the route re-checks server-side regardless. A 403
    // that arrives anyway is handled below like any other failure.
    if (!RetailSession.hasCapability(CAP_REPORTS)) {
        EmptyState(
            Icons.Default.Shield,
            tr("Reports access required"),
            tr("Only accounts with reports access can see takings by employee. " +
                "Ask the owner to grant it."),
        )
        return
    }

    var days by remember { mutableStateOf(30) }
    var rows by remember { mutableStateOf<List<EmployeeSales>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    // Distinct from `rows.isEmpty()`: a load that FAILED and a period with no
    // sales are different facts, and collapsing them is how a screen tells an
    // owner their takings were zero because their session expired.
    var loadError by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    suspend fun load() {
        try {
            val envelope = ApiClient.get().reportByEmployee(days)
            // The VERDICT before the PAYLOAD. A 200 whose envelope says no is a
            // refusal, and rendering it as an empty period would tell an owner
            // their shop took nothing today because their session lapsed.
            val refusal = byEmployeeRefusalOrNull(envelope)
            if (refusal != null) { loadError = tr(refusal); rows = emptyList(); return }
            // `?: throw` rather than `?: emptyList()`. Gson writes an explicit
            // JSON null straight into the field regardless of what the Kotlin
            // type claims (ByEmployeeResponse's doc comment carries the full
            // post-mortem); defaulting it here would trade the crash for a
            // screen that reports a malformed 200 as "nobody sold anything".
            // Throwing puts it on the same path as every other failure.
            rows = envelope.data
                ?: throw IllegalStateException("Malformed response: by-employee data was null")
            loadError = null
        } catch (e: Exception) {
            loadError = missingEndpointKeyOrNull(e)?.let { tr(it) } ?: apiErrorMessage(e)
            rows = emptyList()
        }
    }
    LaunchedEffect(days) { loading = true; load(); loading = false }

    Column(Modifier.fillMaxSize()) {
        LazyRow(
            Modifier.fillMaxWidth().padding(16.dp, 16.dp, 16.dp, 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            items(reportPeriods) { p ->
                FilterChip(
                    selected = days == p.days,
                    onClick = { days = p.days },
                    label = { Text(tr(p.label)) },
                )
            }
        }

        when {
            loading -> SkeletonList()
            loadError != null -> EmptyState(
                Icons.Default.Groups, tr("Couldn't load takings by employee"), loadError!!,
                ctaText = tr("Try again"),
                onCta = { scope.launch { loading = true; load(); loading = false } },
            )
            rows.isEmpty() -> EmptyState(
                Icons.Default.Groups, tr("No sales in this period"),
                tr("Nothing was rung up in the selected period. " +
                    "Choose a longer period to see more."),
            )
            else -> LazyColumn(
                Modifier.fillMaxSize(),
                contentPadding = PaddingValues(16.dp, 8.dp, 16.dp, 24.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                // Deliberately NO `key = { it.actor_user_uid }`. The route
                // contract says there is exactly one null-uid bucket, but a
                // LazyColumn given two identical keys throws ("Key was already
                // used"), so keying on a server-supplied value would turn a
                // broken contract into a crash on the reporting screen. The
                // list is replaced wholesale on every period change anyway, so
                // positional identity costs nothing here.
                items(rows) { row -> EmployeeSalesRow(row) }
                item {
                    Spacer(Modifier.height(6.dp))
                    // Said once, at the bottom, rather than repeated on every
                    // unattributed row: the owner needs to know the total is
                    // complete, not to be told the same caveat five times.
                    Text(
                        tr("Every sale in the period is counted here, including any the " +
                            "app could not attribute."),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

@Composable
private fun EmployeeSalesRow(row: EmployeeSales) {
    val who = rowAttribution(row)
    TillCard(Modifier.fillMaxWidth()) {
        Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
            if (who != Attribution.NAMED) {
                Icon(
                    Icons.AutoMirrored.Filled.HelpOutline, null,
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.width(12.dp))
            }
            Column(Modifier.weight(1f)) {
                Text(
                    when (who) {
                        // bidiIsolate, not raw interpolation: this line sits
                        // in an RTL paragraph on an Arabic till and the
                        // identity is very often Latin.
                        Attribution.NAMED ->
                            bidiIsolate(attributedName(row.employee_id, row.email).orEmpty())
                        Attribution.ACCOUNT_GONE -> tr("Account removed")
                        Attribution.NOT_RECORDED -> tr("Not attributed")
                    },
                    fontWeight = FontWeight.Bold,
                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                    color = if (who == Attribution.NAMED) MaterialTheme.colorScheme.onSurface
                            else MaterialTheme.colorScheme.onSurfaceVariant,
                )
                // The explanation belongs on the row, not in a footnote: an
                // owner scanning a list needs to know THIS line is not a
                // person before they go looking for the employee it names.
                val note = when (who) {
                    Attribution.NAMED -> null
                    Attribution.ACCOUNT_GONE ->
                        tr("The account that rang these sales no longer exists.")
                    Attribution.NOT_RECORDED ->
                        tr("Sales rung before this app recorded who served them.")
                }
                if (note != null) {
                    Text(
                        note,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Text(
                    "${row.transactions}× " + tr("sales") + " · " + money(row.avg_ticket) + " " + tr("per sale"),
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Spacer(Modifier.width(12.dp))
            Text(
                money(row.revenue),
                fontWeight = FontWeight.ExtraBold,
                color = when {
                    // Checked FIRST: a negative figure is money out whoever it
                    // belongs to, and that fact outranks whether the row is
                    // attributed when choosing what colour it wears.
                    isMoneyOut(row.revenue) -> MaterialTheme.colorScheme.error
                    who == Attribution.NAMED -> Success
                    else -> Info
                },
                style = MaterialTheme.typography.titleMedium,
            )
        }
    }
}
