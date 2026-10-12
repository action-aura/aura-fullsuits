@file:OptIn(
    androidx.compose.material3.ExperimentalMaterial3Api::class,
    androidx.compose.foundation.layout.ExperimentalLayoutApi::class,
)

package com.actionaura.retail.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
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
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.actionaura.retail.net.ApiClient
import com.actionaura.retail.net.ApproveCashSessionRequest
import com.actionaura.retail.net.CashMovementRequest
import com.actionaura.retail.net.CashSession
import com.actionaura.retail.net.CloseCashSessionRequest
import com.actionaura.retail.net.CurrentCashSessionFull
import com.actionaura.retail.net.OpenCashSessionRequest
import com.actionaura.retail.net.XReport
import com.actionaura.retail.net.apiErrorMessage
import com.actionaura.retail.ui.components.SectionHeader
import com.actionaura.retail.ui.components.TillCard
import com.actionaura.retail.ui.i18n.Currency
import com.actionaura.retail.ui.i18n.ltrIsolate
import com.actionaura.retail.ui.i18n.money
import com.actionaura.retail.ui.i18n.parseNum
import com.actionaura.retail.ui.i18n.tr
import com.actionaura.retail.ui.pos.MovementCheck
import com.actionaura.retail.ui.pos.MovementType
import com.actionaura.retail.ui.pos.approvableSessions
import com.actionaura.retail.ui.pos.buildMovementRequest
import com.actionaura.retail.ui.pos.closeReveal
import com.actionaura.retail.ui.pos.fromMinor
import com.actionaura.retail.ui.pos.lastClosingCount
import com.actionaura.retail.ui.pos.minorToInput
import com.actionaura.retail.ui.pos.parseAmountMinor
import com.actionaura.retail.ui.pos.purposesFor
import com.actionaura.retail.ui.pos.redactXReport
import com.actionaura.retail.ui.pos.toMinor
import com.actionaura.retail.ui.theme.Danger
import com.actionaura.retail.ui.theme.Success
import com.actionaura.retail.ui.theme.Warning
import kotlinx.coroutines.launch

/**
 * The drawer on the phone: open a shift with a float, Cash In / Cash Out, the X
 * report, and End Shift with a BLIND count -- the desktop's cash-drawer.js,
 * driven through the same server routes. The server does every sum (expected
 * cash, variance); this screen only asks, sends and shows.
 *
 * Reached from More (Finance), shown to people who hold retail.cash.close --
 * the one capability the whole drawer route family is gated on server-side.
 *
 * Why it exists: a handheld-only shop had no shift control at all (the phone
 * could read `cash-sessions/current` and nothing else), so its sales landed in
 * no drawer and there was no float, no Z report and no variance.
 *
 * BLIND COUNT (UI-2): the cashier counts first. The expected figure and the
 * variance appear only AFTER a count is typed and only to someone the SERVER
 * says may approve (`may_approve`). For everyone else the figure is not just
 * hidden but never fetched for End Shift, and the X report is redacted before
 * it is kept (redactXReport). The server records the variance either way.
 */
private enum class DrawerDialog { MOVEMENT, END_SHIFT }

@Composable
fun CashSessionScreen(snackbar: SnackbarHostState) {
    val scope = rememberCoroutineScope()
    val dp = Currency.decimals

    var loading by remember { mutableStateOf(true) }
    var loadError by remember { mutableStateOf<String?>(null) }
    var current by remember { mutableStateOf<CurrentCashSessionFull?>(null) }
    var history by remember { mutableStateOf<List<CashSession>>(emptyList()) }
    var mayApprove by remember { mutableStateOf(false) }          // fails closed
    var xReport by remember { mutableStateOf<XReport?>(null) }
    var dialog by remember { mutableStateOf<DrawerDialog?>(null) }
    var busy by remember { mutableStateOf(false) }
    var outcome by remember { mutableStateOf<String?>(null) }
    var floatText by remember { mutableStateOf("") }
    var floatFromLast by remember { mutableStateOf(false) }
    var endExpected by remember { mutableStateOf<Double?>(null) }
    var heldCount by remember { mutableStateOf(0) }

    suspend fun reload() {
        loading = true
        loadError = null
        try {
            current = ApiClient.get().currentCashSessionFull()
        } catch (e: Exception) {
            current = null
            loadError = apiErrorMessage(e)
        }
        // The list is a nicety (float pre-fill, approvals, may_approve). A failure leaves
        // mayApprove false -- a blind count, never an accidental reveal.
        val list = try { ApiClient.get().cashSessions(20) } catch (e: Exception) { null }
        history = list?.data.orEmpty()
        mayApprove = list?.may_approve == true
        if (current?.data == null && floatText.isBlank()) {
            val last = lastClosingCount(history)
            floatFromLast = last != null
            floatText = last?.let { minorToInput(toMinor(it, dp), dp) } ?: ""
        }
        loading = false
    }
    LaunchedEffect(Unit) { reload() }

    fun fail(e: Exception) { scope.launch { snackbar.showSnackbar(apiErrorMessage(e)) } }

    val session = current?.data

    Box(Modifier.fillMaxSize()) {
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            when {
                loading -> Box(Modifier.fillMaxWidth().padding(30.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
                loadError != null -> TillCard(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(tr("Could not load the cash drawer. Its state is unknown."), fontWeight = FontWeight.SemiBold)
                        Text(loadError ?: "", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        OutlinedButton(onClick = { scope.launch { reload() } }) { Text(tr("Retry")) }
                    }
                }
                session == null -> {
                    // ── No shift open on THIS terminal ───────────────────────────────
                    TillCard(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                            Text(tr("Open Cash Shift"), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                            current?.terminal_short?.takeIf { it.isNotBlank() }?.let {
                                Text(tr("This drawer belongs to") + " " + ltrIsolate(it), style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            if ((current?.other_terminals_open ?: 0) > 0) {
                                Text(tr("Another till in this shop has a drawer open."), style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            OutlinedTextField(
                                value = floatText, onValueChange = { floatText = it; floatFromLast = false },
                                singleLine = true, label = { Text(tr("Opening Float")) },
                                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                                supportingText = if (floatFromLast) ({
                                    Text(tr("Filled in from the last count on this terminal. Change it if the drawer has changed since."))
                                }) else null,
                                modifier = Modifier.fillMaxWidth(),
                            )
                            Button(
                                enabled = !busy,
                                onClick = {
                                    // Blank is a legitimate zero float (the desktop's `parseFloat(value || 0)`);
                                    // anything else must be a non-negative amount at the currency's precision.
                                    val v = if (floatText.isBlank()) 0.0 else parseNum(floatText)
                                    val ok = v != null && v >= 0.0 &&
                                        (v == 0.0 || parseAmountMinor(floatText, dp) != null)
                                    if (!ok || v == null) {
                                        scope.launch { snackbar.showSnackbar(tr("Enter a valid opening float")) }
                                    } else {
                                        busy = true
                                        scope.launch {
                                            try {
                                                val r = ApiClient.get().openCashSession(OpenCashSessionRequest(fromMinor(toMinor(v, dp), dp)))
                                                if (r.status == "success") {
                                                    outcome = null
                                                    snackbar.showSnackbar(tr("Cash shift opened"))
                                                    floatText = ""
                                                    reload()
                                                } else snackbar.showSnackbar(r.message ?: tr("Failed to open shift"))
                                            } catch (e: Exception) { fail(e) } finally { busy = false }
                                        }
                                    }
                                },
                                modifier = Modifier.fillMaxWidth().height(52.dp),
                            ) { Text(tr("Open Shift")) }
                        }
                    }
                    outcome?.let { OutcomeCard(it) }
                }
                else -> {
                    // ── A shift is open ──────────────────────────────────────────────
                    TillCard(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            Text(tr("Shift open"), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold, color = Success)
                            session.terminal_short?.takeIf { it.isNotBlank() }?.let {
                                Text(tr("This drawer belongs to") + " " + ltrIsolate(it), style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            ReportRow(tr("Opening float"), money(session.opening_float))
                            session.opened_at?.let {
                                ReportRow(tr("Opened"), ltrIsolate(it.replace("T", " ").take(16)))
                            }
                        }
                    }
                    outcome?.let { OutcomeCard(it) }
                    Button(onClick = { dialog = DrawerDialog.MOVEMENT }, modifier = Modifier.fillMaxWidth().height(52.dp)) {
                        Text(tr("Cash In / Cash Out"))
                    }
                    OutlinedButton(
                        onClick = {
                            scope.launch {
                                try {
                                    val r = ApiClient.get().xReport(session.id)
                                    xReport = r.data?.let { redactXReport(it, mayApprove) }
                                    if (r.data == null) snackbar.showSnackbar(r.message ?: tr("Could not load the cash drawer. Its state is unknown."))
                                } catch (e: Exception) { fail(e) }
                            }
                        },
                        modifier = Modifier.fillMaxWidth().height(52.dp),
                    ) { Text(tr("X Report")) }
                    OutlinedButton(
                        onClick = {
                            scope.launch {
                                // The expected figure is fetched ONLY for someone allowed to see it.
                                // A cashier's count is therefore blind in memory as well as on screen.
                                endExpected = null
                                if (mayApprove) {
                                    try { endExpected = ApiClient.get().xReport(session.id).data?.expected_cash }
                                    catch (e: Exception) { fail(e); return@launch }
                                }
                                heldCount = try { ApiClient.get().heldSales().data.size } catch (e: Exception) { 0 }
                                dialog = DrawerDialog.END_SHIFT
                            }
                        },
                        modifier = Modifier.fillMaxWidth().height(52.dp),
                    ) { Text(tr("End Shift")) }

                    xReport?.let { r ->
                        TillCard(Modifier.fillMaxWidth()) {
                            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                                Text(tr("X Report"), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                                ReportRow(tr("Opening float"), money(r.opening_float))
                                ReportRow(tr("Cash sales"), money(r.cash_sales))
                                ReportRow(tr("Customer payments (cash)"), money(r.customer_cash_in))
                                ReportRow(tr("Cash refunds"), money(-kotlin.math.abs(r.cash_refunds)))
                                ReportRow(tr("Float in"), money(r.movements.float_in))
                                ReportRow(tr("Float out"), money(-kotlin.math.abs(r.movements.float_out)))
                                ReportRow(tr("Paid in"), money(r.movements.paid_in))
                                ReportRow(tr("Paid out"), money(-kotlin.math.abs(r.movements.paid_out)))
                                if (mayApprove) {
                                    HorizontalDivider()
                                    ReportRow(tr("Expected cash in drawer"), money(r.expected_cash), bold = true)
                                }
                                r.tender_totals.filter { it.key != "cash" && it.value > 0.0 }.forEach { (m, v) ->
                                    ReportRow(m.replaceFirstChar { it.uppercase() }, money(v))
                                }
                                if (!mayApprove) {
                                    Text(tr("The expected cash is shown to the person who approves the count."),
                                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                            }
                        }
                    }
                }
            }

            // ── Drawers waiting for an approver ──────────────────────────────────────
            val waiting = approvableSessions(history, mayApprove)
            if (!loading && waiting.isNotEmpty()) {
                SectionHeader(tr("Waiting for approval"))
                waiting.forEach { s ->
                    TillCard(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            Text(
                                (s.ended_at ?: s.opened_at ?: "").replace("T", " ").take(16) +
                                    (s.terminal_short?.let { " · " + ltrIsolate(it) } ?: ""),
                                fontWeight = FontWeight.SemiBold,
                            )
                            if (s.variance_status == "not_counted") {
                                Text(tr("Force-closed, never counted"), color = Warning)
                            } else {
                                ReportRow(tr("Expected cash"), s.closing_float_expected?.let { money(it) } ?: "—")
                                ReportRow(tr("Counted Cash (physical count)"), s.closing_float_counted?.let { money(it) } ?: "—")
                                ReportRow(tr("Variance (counted − expected)"), s.variance?.let { money(it) } ?: "—", bold = true)
                            }
                            OutlinedButton(
                                enabled = !busy,
                                onClick = {
                                    busy = true
                                    scope.launch {
                                        try {
                                            val body = if (s.variance_status == "not_counted") ApproveCashSessionRequest(true) else ApproveCashSessionRequest()
                                            val r = ApiClient.get().approveCashSession(s.id, body)
                                            snackbar.showSnackbar(if (r.status == "success") tr("Variance accepted.") else (r.message ?: tr("Failed to accept the variance")))
                                            reload()
                                        } catch (e: Exception) { fail(e) } finally { busy = false }
                                    }
                                },
                            ) { Text(if (s.variance_status == "not_counted") tr("Acknowledge uncounted") else tr("Accept variance")) }
                        }
                    }
                }
            }
        }
    }

    // ── Dialogs ─────────────────────────────────────────────────────────────────────
    if (dialog == DrawerDialog.MOVEMENT && session != null) {
        MovementDialog(
            busy = busy,
            onDismiss = { dialog = null },
            onSubmit = { req ->
                busy = true
                scope.launch {
                    try {
                        val r = ApiClient.get().cashMovement(session.id, req)
                        if (r.status == "success") {
                            dialog = null
                            xReport = null
                            snackbar.showSnackbar(tr("Cash movement recorded"))
                            reload()
                        } else snackbar.showSnackbar(r.message ?: tr("Failed to record movement"))
                    } catch (e: Exception) { fail(e) } finally { busy = false }
                }
            },
        )
    }
    if (dialog == DrawerDialog.END_SHIFT && session != null) {
        EndShiftDialog(
            busy = busy, mayApprove = mayApprove, expected = endExpected, heldCount = heldCount,
            onDismiss = { dialog = null },
            onSubmit = { counted ->
                busy = true
                scope.launch {
                    try {
                        val r = ApiClient.get().closeCashSession(session.id, CloseCashSessionRequest(counted))
                        if (r.status == "success") {
                            dialog = null
                            xReport = null
                            val rep = r.data?.report
                            val msg = if (rep?.variance_status == "approved") tr("Shift ended and variance accepted.")
                                      else tr("Shift ended. The variance is recorded and awaiting approval.")
                            // The variance is shown only to an approver: a cashier who is short must not be
                            // told by the confirmation what the till expected.
                            outcome = if (mayApprove && rep?.variance != null) "$msg ${money(rep.variance)}" else msg
                            reload()
                        } else snackbar.showSnackbar(r.message ?: tr("Failed to end shift"))
                    } catch (e: Exception) { fail(e) } finally { busy = false }
                }
            },
        )
    }
}

@Composable
private fun ReportRow(label: String, value: String, bold: Boolean = false) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(label, Modifier.weight(1f), fontWeight = if (bold) FontWeight.Bold else FontWeight.Normal)
        Text(value, fontWeight = if (bold) FontWeight.Bold else FontWeight.SemiBold)
    }
}

@Composable
private fun OutcomeCard(text: String) {
    TillCard(Modifier.fillMaxWidth()) {
        Text(text, Modifier.padding(16.dp), fontWeight = FontWeight.SemiBold)
    }
}

private fun movementLabel(t: MovementType): String = when (t) {
    MovementType.FLOAT_IN -> tr("Float In (add cash to drawer)")
    MovementType.FLOAT_OUT -> tr("Float Out (remove cash from drawer)")
    MovementType.PAID_IN -> tr("Paid In (misc. cash received)")
    MovementType.PAID_OUT -> tr("Paid Out (misc. cash paid)")
}

@Composable
private fun MovementDialog(busy: Boolean, onDismiss: () -> Unit, onSubmit: (CashMovementRequest) -> Unit) {
    var type by remember { mutableStateOf(MovementType.PAID_OUT) }
    var purpose by remember { mutableStateOf<String?>(null) }
    var menu by remember { mutableStateOf(false) }
    var amount by remember { mutableStateOf("") }
    var reason by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    val purposes = purposesFor(type)

    AlertDialog(
        onDismissRequest = { if (!busy) onDismiss() },
        title = { Text(tr("Cash Movement")) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    MovementType.entries.forEach { t ->
                        FilterChip(
                            selected = type == t,
                            // The two lists share one select: a switch clears the choice, so a paid-in
                            // can never carry an expense code left over from a paid-out.
                            onClick = { type = t; purpose = null; error = null },
                            label = { Text(movementLabel(t)) },
                        )
                    }
                }
                if (purposes != null) {
                    Text(tr("What for?"), style = MaterialTheme.typography.labelMedium)
                    Box {
                        OutlinedButton(onClick = { menu = true }, modifier = Modifier.fillMaxWidth()) {
                            Text(
                                purposes.firstOrNull { it.code == purpose }?.let { tr(it.label) }
                                    ?: tr("Choose what the money was for"),
                                Modifier.weight(1f),
                            )
                        }
                        DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                            purposes.forEach { p ->
                                DropdownMenuItem(
                                    text = { Text(tr(p.label)) },
                                    onClick = { purpose = p.code; menu = false; error = null },
                                )
                            }
                        }
                    }
                }
                OutlinedTextField(
                    value = amount, onValueChange = { amount = it; error = null }, singleLine = true,
                    label = { Text(tr("Amount")) },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = reason, onValueChange = { reason = it.take(200) }, singleLine = true,
                    label = { Text(tr("Reason")) },
                    placeholder = { Text(tr("Optional note")) },
                    modifier = Modifier.fillMaxWidth(),
                )
                error?.let { Text(it, color = Danger, style = MaterialTheme.typography.bodySmall) }
            }
        },
        confirmButton = {
            Button(
                enabled = !busy,
                onClick = {
                    when (val c = buildMovementRequest(type, amount, reason, purpose, Currency.decimals)) {
                        is MovementCheck.Ok -> onSubmit(c.request)
                        MovementCheck.BadAmount -> error = tr("Enter a valid amount")
                        MovementCheck.PurposeRequired -> error = tr("Choose what the money was for")
                    }
                },
            ) { Text(tr("Record")) }
        },
        dismissButton = { TextButton(onClick = onDismiss, enabled = !busy) { Text(tr("Cancel")) } },
    )
}

/**
 * End Shift. COUNT FIRST. The expected figure and the variance are rendered
 * only when [closeReveal] says so -- a count has been typed AND the server said
 * this person may approve. For everyone else [expected] is null (never fetched)
 * and no node anywhere in this dialog can show it.
 */
@Composable
private fun EndShiftDialog(
    busy: Boolean, mayApprove: Boolean, expected: Double?, heldCount: Int,
    onDismiss: () -> Unit, onSubmit: (Double) -> Unit,
) {
    var countText by remember { mutableStateOf("") }
    val dp = Currency.decimals
    // Zero is a real count (an empty drawer), so this accepts "0" -- unlike a movement amount.
    val countedMinor: Long? = when {
        countText.isBlank() -> null
        else -> parseNum(countText)?.takeIf { it >= 0.0 && it.isFinite() }
            ?.let { v -> if (v == 0.0) 0L else parseAmountMinor(countText, dp) }
    }
    val reveal = closeReveal(hasCount = countedMinor != null, mayApprove = mayApprove)
    val expectedMinor = expected?.let { toMinor(it, dp) }

    AlertDialog(
        onDismissRequest = { if (!busy) onDismiss() },
        title = { Text(tr("End Shift — Z Report")) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                if (heldCount > 0) {
                    Text(
                        tr("{n} held sale(s) are still parked. Ending the shift keeps them, but check them before you leave.")
                            .replace("{n}", heldCount.toString()),
                        style = MaterialTheme.typography.bodySmall, color = Warning,
                    )
                }
                OutlinedTextField(
                    value = countText, onValueChange = { countText = it }, singleLine = true,
                    label = { Text(tr("Counted Cash (physical count)")) },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                    modifier = Modifier.fillMaxWidth(),
                )
                if (reveal.showExpected && expectedMinor != null) {
                    Row(Modifier.fillMaxWidth()) {
                        Text(tr("Expected cash"), Modifier.weight(1f))
                        Text(money(fromMinor(expectedMinor, dp)), fontWeight = FontWeight.SemiBold)
                    }
                    if (reveal.showVariance && countedMinor != null) {
                        val v = countedMinor - expectedMinor
                        Row(Modifier.fillMaxWidth()) {
                            Text(tr("Variance (counted − expected)"), Modifier.weight(1f))
                            Text(money(fromMinor(v, dp)), fontWeight = FontWeight.Bold,
                                color = if (v == 0L) Success else if (v < 0L) Danger else Warning)
                        }
                    }
                }
                Text(
                    tr("Ending locks this count. A short drawer never blocks you from ending your shift. The variance is recorded and someone with approval authority reviews it."),
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        },
        confirmButton = {
            Button(
                enabled = !busy && countedMinor != null,
                onClick = { countedMinor?.let { onSubmit(fromMinor(it, dp)) } },
            ) { Text(tr("End Shift")) }
        },
        dismissButton = { TextButton(onClick = onDismiss, enabled = !busy) { Text(tr("Cancel")) } },
    )
}
