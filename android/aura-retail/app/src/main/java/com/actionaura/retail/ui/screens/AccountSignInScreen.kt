package com.actionaura.retail.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.actionaura.retail.licensing.AccountFlowStore
import com.actionaura.retail.licensing.AccountStep
import com.actionaura.retail.licensing.ContractGate
import com.actionaura.retail.licensing.LicensingCoordinator
import com.actionaura.retail.licensing.RolePendingStore
import com.actionaura.retail.licensing.accountReasonKey
import com.actionaura.retail.ui.i18n.tr
import kotlinx.coroutines.launch

/**
 * "Sign in with your Aura account" -- the first way to activate a phone (new
 * customer flow, plan S6). The Windows twin is licensing-account.js; the order of
 * the steps, the wording and the contract rules are the same on purpose.
 *
 *   sign in -> (forced password change) -> (contract) -> (branch) -> activate
 *
 * The order is enforced by [com.actionaura.retail.licensing.AccountFlowSession]
 * (pure, unit-tested) and by Owner, not by this screen -- a Composable is not
 * unit-testable here, so this file holds layout and nothing that decides.
 *
 * The typed password is blanked from the field in the same breath as the call is
 * made; it is never put in `rememberSaveable` (that is written to disk by the
 * platform), a log, or anything that outlives the click.
 */
@Composable
fun AccountSignInPanel(
    coordinator: LicensingCoordinator,
    snackbar: SnackbarHostState? = null,
    onActivated: () -> Unit,
) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var step by remember { mutableStateOf(AccountStep.SIGN_IN) }
    var email by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var newPassword by remember { mutableStateOf("") }
    var newPassword2 by remember { mutableStateOf("") }
    var chosen by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    fun fail(result: Map<String, Any?>) {
        val code = result["reason_code"] as? String
        error = tr(accountReasonKey(code, result["policy_code"] as? String))
        // Anything that kills the ticket sends the person back to the start.
        if (code == "ACCOUNT_FLOW_EXPIRED" || code == "ACCOUNT_SIGN_IN_FAILED" && step != AccountStep.SIGN_IN) {
            AccountFlowStore.clear(); step = AccountStep.SIGN_IN
        }
    }

    fun advance() { step = AccountFlowStore.get()?.nextStep() ?: AccountStep.SIGN_IN }

    Column(verticalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.fillMaxWidth()) {
        when (step) {
            AccountStep.SIGN_IN -> {
                Text(
                    tr("Sign in with your Aura account to activate this computer. Use the email and password your seller gave you."),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                OutlinedTextField(
                    value = email, onValueChange = { email = it }, singleLine = true, enabled = !busy,
                    label = { Text(tr("Email")) },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email),
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = password, onValueChange = { password = it }, singleLine = true, enabled = !busy,
                    label = { Text(tr("Password")) },
                    visualTransformation = PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                    modifier = Modifier.fillMaxWidth(),
                )
                Button(
                    enabled = !busy && email.isNotBlank() && password.isNotEmpty(),
                    onClick = {
                        val typedEmail = email.trim()
                        val typedPassword = password
                        password = ""   // blanked at once; the call below owns the only other copy
                        scope.launch {
                            error = null; busy = true
                            val result = try { coordinator.accountSignIn(typedEmail, typedPassword) } catch (e: Exception) {
                                mapOf<String, Any?>("reason_code" to "NETWORK_UNAVAILABLE")
                            }
                            busy = false
                            if (result["reason_code"] != null) fail(result)
                            else { email = typedEmail; advance() }
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    if (busy) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.onPrimary)
                    else Text(tr("Sign in"))
                }
            }

            AccountStep.PASSWORD -> {
                Text(tr("Choose your own password"), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                Text(
                    tr("The password your seller gave you was temporary. Choose a new one that only you know. At least 10 characters."),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                OutlinedTextField(
                    value = newPassword, onValueChange = { newPassword = it }, singleLine = true, enabled = !busy,
                    label = { Text(tr("New password")) },
                    visualTransformation = PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = newPassword2, onValueChange = { newPassword2 = it }, singleLine = true, enabled = !busy,
                    label = { Text(tr("Repeat the new password")) },
                    visualTransformation = PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                    modifier = Modifier.fillMaxWidth(),
                )
                Button(
                    enabled = !busy && newPassword.isNotEmpty(),
                    onClick = {
                        if (newPassword != newPassword2) {
                            error = tr("The two passwords are not the same."); return@Button
                        }
                        val typed = newPassword
                        newPassword = ""; newPassword2 = ""
                        scope.launch {
                            error = null; busy = true
                            val result = try { coordinator.changePassword(typed) } catch (e: Exception) {
                                mapOf<String, Any?>("reason_code" to "NETWORK_UNAVAILABLE")
                            }
                            busy = false
                            if (result["reason_code"] != null) fail(result) else advance()
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    if (busy) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.onPrimary)
                    else Text(tr("Save password"))
                }
            }

            AccountStep.CONTRACT -> {
                Text(tr("One last step: please read and accept the service agreement."),
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                val session = AccountFlowStore.get()
                if (session != null) {
                    ContractDialog(
                        title = session.view.contract.title,
                        body = session.view.contract.body.orEmpty(),
                        rtl = session.view.contract.isRtl,
                        draft = session.view.contract.draft,
                        busy = busy,
                        error = error,
                        onAccept = { idDocuments, dataOutsideJordan ->
                            scope.launch {
                                error = null; busy = true
                                val result = try { coordinator.acceptContract(idDocuments, dataOutsideJordan) } catch (e: Exception) {
                                    mapOf<String, Any?>("reason_code" to "NETWORK_UNAVAILABLE")
                                }
                                busy = false
                                if (result["reason_code"] != null) fail(result) else advance()
                            }
                        },
                        onDecline = { AccountFlowStore.clear(); error = null; step = AccountStep.SIGN_IN },
                    )
                } else step = AccountStep.SIGN_IN
            }

            AccountStep.BRANCH, AccountStep.ACTIVATE -> {
                val session = AccountFlowStore.get()
                if (session == null) { step = AccountStep.SIGN_IN } else {
                    val licences = session.view.licences
                    if (licences.size > 1) {
                        Text(tr("Which branch is this computer for?"), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                        licences.forEachIndexed { i, lic ->
                            val label = com.actionaura.retail.licensing.licenceDisplayLabel(
                                lic, tr("Branch") + " " + (i + 1), com.actionaura.retail.ui.i18n.AppLocale.isRtl, alwaysFallback = true) +
                                (lic.trialEndsOn?.let { " · " + tr("trial ends") + " " + it } ?: "")
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                RadioButton(selected = chosen == lic.licensePublicId, onClick = { chosen = lic.licensePublicId })
                                Text(label)
                            }
                        }
                    } else {
                        val lic = licences.first()
                        chosen = lic.licensePublicId
                        Text(tr("Signed in. This computer is ready to be activated for:"), color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Text(com.actionaura.retail.licensing.licenceDisplayLabel(
                                lic, tr("your shop"), com.actionaura.retail.ui.i18n.AppLocale.isRtl),
                            style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                        lic.trialEndsOn?.let { Text(tr("Your free trial runs until") + " " + it + ".") }
                    }
                    Button(
                        enabled = !busy && chosen != null,
                        onClick = {
                            scope.launch {
                                error = null; busy = true
                                val result = try { coordinator.activateAccount(chosen) } catch (e: Exception) {
                                    mapOf<String, Any?>("reason_code" to "NETWORK_UNAVAILABLE")
                                }
                                busy = false
                                if (result["result"] == "SUCCESS") {
                                    // The role step ("which computer is this?") is owed from now on.
                                    RolePendingStore(ctx).mark(email)
                                    onActivated()
                                } else if (result["result"] == "PENDING") {
                                    error = tr("Your request was received and is waiting for approval from Action Aura.")
                                } else {
                                    fail(result)
                                    if (result["reason_code"] == "ACTIVATION_GRANT_INVALID") step = AccountStep.SIGN_IN
                                }
                            }
                        },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        if (busy) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.onPrimary)
                        else Text(tr("Activate this computer"))
                    }
                }
            }
        }
        if (step != AccountStep.CONTRACT) {
            error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        }
    }
}

/**
 * The service agreement as a dialog that cannot be dismissed: no back button, no
 * tap outside. Arabic first (the heading is Arabic whatever language the app is
 * in), the box is locked until the text has been scrolled to its end, Accept is
 * locked until the box is ticked, and text nobody has lawyer-approved carries a
 * DRAFT watermark. The rules themselves are [ContractGate]'s (unit-tested).
 */
@Composable
private fun ContractDialog(
    title: String?,
    body: String,
    rtl: Boolean,
    draft: Boolean,
    busy: Boolean,
    error: String?,
    onAccept: (idDocuments: Boolean, dataOutsideJordan: Boolean) -> Unit,
    onDecline: () -> Unit,
) {
    val scroll = rememberScrollState()
    var reachedEnd by remember { mutableStateOf(false) }
    var ticked by remember { mutableStateOf(false) }
    // The two SEPARATE consents (Owner 2026-11-01), both unticked on arrival. Outside-Jordan is
    // mandatory; ID documents is optional. Neither waits for the scroll.
    var idDocumentsTicked by remember { mutableStateOf(false) }
    var dataOutsideJordanTicked by remember { mutableStateOf(false) }
    // Latched: once the end was seen it stays seen (scrolling back up must not re-lock it).
    LaunchedEffect(scroll.value, scroll.maxValue) {
        if (ContractGate.reachedEnd(scroll.value, scroll.maxValue)) reachedEnd = true
    }

    Dialog(
        onDismissRequest = {},
        properties = DialogProperties(dismissOnBackPress = false, dismissOnClickOutside = false),
    ) {
        Surface(shape = MaterialTheme.shapes.large, tonalElevation = 6.dp) {
            Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Rtl) {
                    Text(title ?: "عقد الاستخدام", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                    Text("يرجى قراءة العقد حتى نهايته، ثم تأكيد الموافقة للمتابعة.", style = MaterialTheme.typography.bodySmall)
                }
                Text(tr("Read the agreement to the end, then tick the box to continue."), style = MaterialTheme.typography.bodySmall)

                Box(Modifier.weight(1f, fill = false).heightIn(min = 140.dp, max = 360.dp)) {
                    CompositionLocalProvider(LocalLayoutDirection provides if (rtl) LayoutDirection.Rtl else LayoutDirection.Ltr) {
                        Column(Modifier.verticalScroll(scroll)) { Text(body, lineHeight = 24.sp) }
                    }
                    if (draft) {
                        Text(
                            "DRAFT — مسودة",
                            modifier = Modifier.align(Alignment.Center).rotate(-18f).alpha(0.2f),
                            style = MaterialTheme.typography.displaySmall,
                            fontWeight = FontWeight.ExtraBold,
                            color = MaterialTheme.colorScheme.error,
                        )
                    }
                }

                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(checked = ticked, enabled = ContractGate.boxEnabled(reachedEnd), onCheckedChange = { ticked = it })
                    Column {
                        CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Rtl) {
                            Text("قرأت العقد وأوافق على جميع بنوده", style = MaterialTheme.typography.bodyMedium)
                        }
                        Text(tr("I have read the agreement and I accept it"), style = MaterialTheme.typography.bodySmall)
                    }
                }
                if (!reachedEnd) {
                    Text(tr("Scroll to the end of the agreement to enable the box."), style = MaterialTheme.typography.bodySmall)
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(checked = idDocumentsTicked, onCheckedChange = { idDocumentsTicked = it })
                    Column {
                        CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Rtl) {
                            Text("أوافق على احتفاظ Action Aura بصورتي هويتي وسجلي التجاري للتحقق من حسابي (تُحذف بعد سنة من انتهاء اشتراكي).", style = MaterialTheme.typography.bodyMedium)
                        }
                        Text(tr("I agree that Action Aura keeps the photos of my ID and commercial registration to verify my account (deleted one year after my subscription ends)."), style = MaterialTheme.typography.bodySmall)
                    }
                }
                if (ContractGate.idDocumentsNoteVisible(idDocumentsTicked)) {
                    Column(Modifier.padding(start = 12.dp)) {
                        CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Rtl) {
                            Text("بدون هذه الموافقة سيتحقق فريقنا من هويتك هاتفيًا.", style = MaterialTheme.typography.bodySmall)
                        }
                        Text(tr("Without this, our team will verify your identity by phone."), style = MaterialTheme.typography.bodySmall)
                    }
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(checked = dataOutsideJordanTicked, onCheckedChange = { dataOutsideJordanTicked = it })
                    Column {
                        CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Rtl) {
                            Text("أوافق على حفظ بيانات حسابي ومتجري على خوادم Action Aura خارج الأردن (ألمانيا وCloudflare).", style = MaterialTheme.typography.bodyMedium)
                        }
                        Text(tr("I agree that my account and shop data are stored on Action Aura's servers outside Jordan (Germany and Cloudflare)."), style = MaterialTheme.typography.bodySmall)
                    }
                }
                error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                // The shop owner (or their authorised person) accepts in person -- never the seller.
                CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Rtl) {
                    Text("يجب أن يوافق صاحب المتجر أو المفوَّض بنفسه، ولا يجوز للمندوب الموافقة نيابة عنه.", style = MaterialTheme.typography.bodySmall)
                }
                Text(tr("The shop owner (or their authorised person) must accept personally; the seller may not accept on their behalf."), style = MaterialTheme.typography.bodySmall)
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Button(
                        enabled = !busy && ContractGate.acceptEnabled(reachedEnd, ticked, dataOutsideJordanTicked),
                        onClick = { onAccept(idDocumentsTicked, dataOutsideJordanTicked) },
                    ) { Text("أوافق / " + tr("Accept")) }
                    OutlinedButton(enabled = !busy, onClick = onDecline) { Text(tr("Decline and go back")) }
                }
            }
        }
    }
}
