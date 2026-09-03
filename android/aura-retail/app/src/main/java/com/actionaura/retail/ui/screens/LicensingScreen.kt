@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.actionaura.retail.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.VerifiedUser
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.actionaura.retail.licensing.ActivationOutcome
import com.actionaura.retail.licensing.ActivationPollSchedule
import com.actionaura.retail.licensing.LicensingCoordinator
import com.actionaura.retail.licensing.LicensingMessages
import com.actionaura.retail.licensing.PendingActivationRecord
import com.actionaura.retail.licensing.PendingActivationStore
import com.actionaura.retail.licensing.classifyActivationResult
import com.actionaura.retail.licensing.reachedOwner
import com.actionaura.retail.ui.i18n.tr
import com.actionaura.retail.ui.theme.Danger
import com.actionaura.retail.ui.theme.DangerContainer
import com.actionaura.retail.ui.theme.Success
import com.actionaura.retail.ui.theme.SuccessContainer
import com.actionaura.retail.ui.theme.TextTertiary
import com.actionaura.retail.ui.theme.Warning
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.text.DateFormat
import java.util.Date

/**
 * Activation/status UI (Phase 7 Part G, Android). Talks only to
 * [LicensingCoordinator] -- never to DeviceIdentity/OwnerClient directly,
 * and never assumes an outcome beyond what the coordinator's return value
 * (itself only ever the embedded Python backend's own verified opinion)
 * says. Mirrors products/retail/frontend/licensing.js's state labels,
 * reason-code messages AND its awaiting-approval screen, so the activation
 * experience reads and behaves the same on Windows and Android.
 *
 * The reason-code copy and the poll's decision table deliberately do NOT
 * live in this file -- see com.actionaura.retail.licensing.LicensingMessages.
 * They are the part of this screen that can actually be wrong, and a
 * @Composable is not unit-testable in this project's test environment.
 *
 * KNOWN GAP, stated rather than hidden: every string here goes through tr(),
 * but most of this surface has no entry in ui/i18n/Strings.kt's Arabic map, so
 * an Arabic user sees English here and RTL layout around it. The three lines
 * that make a factual claim about what the app just did ("checking
 * automatically", "last checked at X", "could not reach the service") ARE
 * translated, because those are the ones a customer reads back to support.
 * The remaining licensing copy -- including LicensingMessages' reason-code
 * table -- still needs a real Arabic pass.
 */

// Every branch below used to be a raw hex literal bypassing the token layer
// entirely (AUDIT: colour-token-parity). Measured against SurfaceApp, the app
// shell's own background -- this AssistChip's container is transparent, so
// its icon tint (the only place this Pair's colour lands) is read straight
// off whatever the app surface is -- ALL FOUR of the original literals
// failed WCAG AA (4.5:1 floor):
//   #666666 (grey/neutral)  3.24:1
//   #1A7A3D (green/active)  3.45:1
//   #8A6100 (amber/warning) 3.36:1
//   #A3231F (red/danger)    2.50:1
// Not just the grey one the audit that found this started from -- every
// state on this screen, including "Active", was under-contrast. Mapped to
// the token whose MEANING matches each group (TextTertiary is the token
// this codebase already uses for de-emphasised/meta status, which is what
// "not yet configured / mid-activation / deactivated" are); all four now
// measure 8.17-11.28:1 on SurfaceApp.
private fun stateLabel(state: String): Pair<String, Color> = when (state) {
    "NOT_CONFIGURED" -> "Not configured" to TextTertiary
    "ACTIVATION_REQUIRED" -> "Activation required" to TextTertiary
    "ACTIVATING" -> "Activating…" to TextTertiary
    "ACTIVE_ONLINE" -> "Active" to Success
    "ACTIVE_OFFLINE" -> "Active (offline)" to Success
    "WARNING" -> "Check-in needed soon" to Warning
    "GRACE_PERIOD" -> "Offline grace period" to Warning
    "RESTRICTED" -> "Restricted" to Danger
    "SUSPENDED" -> "Suspended" to Danger
    "REVOKED" -> "Revoked" to Danger
    "EXPIRED" -> "Expired" to Danger
    "DEVICE_DEACTIVATED" -> "Device deactivated" to TextTertiary
    "CLOCK_REVIEW_REQUIRED" -> "Clock review required" to Warning
    "LOCAL_STATE_CORRUPT" -> "Local state needs reset" to Danger
    else -> state to TextTertiary
}

/**
 * Translated copy for a reason code, with an HONEST fallback. A code this
 * build has no message for names itself instead of borrowing
 * ACTIVATION_REJECTED's "double-check the key" line -- that line asserts a
 * cause ("your key") this build has no way to know, and it is precisely
 * what sent customers to re-type a perfectly good key forever.
 */
private fun reasonText(reason: String?): String =
    LicensingMessages.reasonMessage(reason)?.let { tr(it) }
        ?: tr(LicensingMessages.UNKNOWN_REASON_TEMPLATE).format(reason ?: "UNKNOWN")

private fun nowTimeLabel(): String = DateFormat.getTimeInstance(DateFormat.SHORT).format(Date())

@Composable
fun LicensingScreen(onBack: () -> Unit, snackbar: SnackbarHostState, onActivated: (() -> Unit)? = null) {
    val ctx = LocalContext.current
    val coordinator = remember { LicensingCoordinator(ctx) }
    val pendingStore = remember { PendingActivationStore(ctx) }
    val scope = rememberCoroutineScope()

    var status by remember { mutableStateOf<Map<String, Any?>>(mapOf("current_state" to "NOT_CONFIGURED")) }
    var loading by remember { mutableStateOf(true) }
    var busy by remember { mutableStateOf(false) }
    var licenseKeyInput by remember { mutableStateOf("") }
    var errorMessage by remember { mutableStateOf<String?>(null) }
    var infoMessage by remember { mutableStateOf<String?>(null) }
    var confirmDeactivate by remember { mutableStateOf(false) }

    // The marker that says "a key from this installation is already awaiting
    // approval" -- persisted (PendingActivationStore), so it survives the
    // process. The submitted KEY is held only here, in composition memory,
    // and is NEVER written anywhere: it is credential material, and the
    // backend goes out of its way to stop holding it (routes.py's activate()
    // nulls it in a `finally`). `remember`, deliberately not
    // `rememberSaveable` -- saved instance state is written to disk by the
    // platform, which would quietly undo exactly that.
    //
    // The accepted consequence: after a process death or a rotation the key
    // is gone while the marker survives. That is not papered over -- the key
    // form is re-shown, with framing that says the first submission was not
    // lost. Same trade licensing.js's `pendingKey` takes, for the same reason.
    var pendingRecord by remember { mutableStateOf<PendingActivationRecord?>(null) }
    var pendingKey by remember { mutableStateOf("") }
    var lastCheckedLabel by remember { mutableStateOf<String?>(null) }

    // The automatic poll has given up after ActivationPollSchedule
    // .MAX_CONSECUTIVE_UNREACHED failures to reach the service, and the screen
    // must now say so rather than keep claiming to be checking. Cleared by a
    // manual "Check Now", which also restarts the loop (see pollAttempt).
    var pollExhausted by remember { mutableStateOf(false) }
    // Bumped by "Check Now" purely to re-key the polling LaunchedEffect, which
    // is how a user press restarts a poll that had given up.
    var pollAttempt by remember { mutableStateOf(0) }

    suspend fun refresh() {
        status = try { coordinator.status() } catch (e: Exception) { mapOf("current_state" to "NOT_CONFIGURED") }
    }

    /**
     * One activation attempt -- the first submission from the key form, an
     * automatic poll tick, and the manual "Check Now" press all go through
     * here, so the five outcomes cannot be handled one way on one path and
     * differently on another.
     *
     * `fromUser` is true only for a real button press. Automatic ticks stay
     * silent about TRANSIENT failures on purpose: a network blip every 30s
     * must not paint the screen red, because nothing is actually wrong with
     * the pending activation.
     *
     * Returns whether this attempt actually reached the licensing service,
     * which is what the caller's backoff counts (see [ActivationPollSchedule]).
     */
    suspend fun attemptActivation(key: String, fromUser: Boolean): Boolean {
        val wasAwaiting = pendingKey.isNotBlank()
        val result = try { coordinator.activate(key) } catch (e: Exception) {
            mapOf<String, Any?>("reason_code" to "NETWORK_UNAVAILABLE")
        }
        val outcome = classifyActivationResult(result)

        // AFTER classification, and only when the service actually answered.
        // Assigning this before the `when` (as this did) advanced the
        // on-screen "Last checked at HH:MM" on a tick that never left the
        // device -- telling the user their licence had just been checked when
        // nothing had been checked since they last had signal.
        if (outcome.reachedOwner) lastCheckedLabel = nowTimeLabel()

        when (outcome) {
            is ActivationOutcome.Approved -> {
                // The marker and the held key have both done their job; leaving
                // them set would show the awaiting screen again the next time
                // this device legitimately needs a key.
                pendingStore.clear(); pendingRecord = null; pendingKey = ""
                errorMessage = null
                infoMessage = tr("Activation successful.")
                refresh()
                // Pre-login gate usage (AppRoot's Phase.LICENSE) passes this
                // to advance to the login screen; the settings-accessed path
                // (already logged in) passes null and just stays here showing
                // the now-active status.
                onActivated?.invoke()
            }

            is ActivationOutcome.StillPending -> {
                // Phase 8 Part O: Owner is holding this activation for manual
                // approval, not rejecting it -- a distinct, non-error state.
                pendingStore.mark(outcome.installationId)
                pendingRecord = pendingStore.read()
                pendingKey = key
                errorMessage = null
                infoMessage = if (wasAwaiting) null
                    else tr("Your license key was received and is waiting for approval from Action Aura.")
                refresh()
            }

            is ActivationOutcome.Transient -> {
                // "We could not get an answer", never "the answer is no". The
                // held activation survives untouched -- dropping the marker on
                // a DNS blip would strand the user back on a key form for a
                // submission Owner is still perfectly willing to approve.
                if (fromUser) errorMessage = reasonText(outcome.reason)
                if (!wasAwaiting) refresh()
            }

            is ActivationOutcome.LocalVerificationFailed -> {
                // Owner APPROVED and this device could not verify or persist
                // that answer. Keep the marker, keep the key, keep polling --
                // unlike a rejection this self-heals without the user touching
                // anything (a refreshed trust anchor, a corrected clock). Speak
                // up on EVERY tick, not just a button press: staying silent
                // after Owner has already approved would leave the screen
                // claiming "still waiting" for a wait that is over.
                errorMessage = reasonText(outcome.reason)
                infoMessage = null
                if (!wasAwaiting) refresh()
            }

            is ActivationOutcome.Declined -> {
                // A verdict. The marker's whole job was to stop this screen
                // asking for a key that had already been accepted for review;
                // once the review says no, that job is over, and leaving it set
                // would park the user on "waiting for approval" for an approval
                // that is never coming.
                pendingStore.clear(); pendingRecord = null; pendingKey = ""
                infoMessage = null
                errorMessage = reasonText(outcome.reason)
                refresh()
            }
        }
        return outcome.reachedOwner
    }

    LaunchedEffect(Unit) {
        loading = true
        refresh()
        pendingRecord = pendingStore.read()
        loading = false
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(tr("Licensing")) },
                navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.Default.ArrowBack, null) } },
            )
        },
    ) { padding ->
        Column(
            Modifier.padding(padding).fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            if (loading) {
                Box(Modifier.fillMaxWidth().padding(32.dp), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator()
                }
                return@Column
            }

            errorMessage?.let { MessageBanner(it, isError = true) }
            infoMessage?.let { MessageBanner(it, isError = false) }

            val state = status["current_state"] as? String ?: "NOT_CONFIGURED"

            // ── Awaiting-approval screen ──────────────────────────────────
            // Reached when this device still needs activation AND a key was
            // already submitted and came back 202 PENDING. Without it the user
            // was hard-trapped: on PENDING the backend deliberately persists NO
            // local state record (commercial_runtime/licensing_contracts/
            // activation.py), so status() keeps answering a bare
            // NOT_CONFIGURED, AppRoot's boot gate keeps routing here, and this
            // screen kept re-rendering the key form. The only move available
            // was to submit the SAME key again, collect another 202, and be
            // shown the form again. Forever.
            //
            // It polls /activate and NOT check-in, which is the non-obvious
            // part: routes.py's check-in short-circuits with a canned
            // ACTIVATION_REQUIRED for exactly the `state_repository.load() is
            // None` case a PENDING device is in, without making any Owner call
            // at all -- so polling check-in could observe neither an approval
            // nor a rejection, and would have shipped a fresh false promise of
            // exactly the class this screen exists to remove. Re-POSTing
            // /activate is the only thing that resolves a held activation
            // (Owner self-heals a repeat rather than opening a second request).
            // That is why this screen runs on the submitted KEY, and why it is
            // not shown without one.
            if (state in NEEDS_ACTIVATION_STATES && pendingRecord != null && pendingKey.isNotBlank()) {
                val (label, color) = stateLabel("ACTIVATING")
                AssistChip(onClick = {}, enabled = false, label = { Text(tr(label)) },
                    leadingIcon = { Icon(Icons.Default.VerifiedUser, null, tint = color) })

                Text(
                    tr("Your license key was received. This activation is waiting for approval from Action Aura " +
                        "before this installation can be used. You do not need to enter the key again."),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    when {
                        // The poll has stopped. Saying "checking automatically"
                        // now would be the same false promise this whole screen
                        // was built to remove, one layer deeper.
                        pollExhausted -> tr(
                            "Could not reach the licensing service. Your key is still held for approval — " +
                                "press Check Now to try again when you are back online."
                        )
                        lastCheckedLabel != null ->
                            tr("Still waiting for approval. Last checked at %s.").format(lastCheckedLabel)
                        else -> tr("Checking with the licensing service automatically every %s seconds…")
                            .format(ActivationPollSchedule.BASE_INTERVAL_MS / 1000)
                    },
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )

                val installationId = (status["installation_id"] as? String) ?: pendingRecord?.installationId
                installationId?.let {
                    ElevatedCard(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(16.dp)) { StatusRow(tr("Installation"), it) }
                    }
                }

                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    OutlinedButton(
                        enabled = !busy,
                        onClick = {
                            scope.launch {
                                errorMessage = null; busy = true
                                try {
                                    attemptActivation(pendingKey, fromUser = true)
                                } finally {
                                    busy = false
                                }
                                // A press is also how a poll that gave up is
                                // restarted: re-keying the effect below starts
                                // a fresh streak from the base interval.
                                pollExhausted = false
                                pollAttempt++
                            }
                        },
                    ) { Text(tr("Check Now")) }

                    // The escape hatch, and the reason this screen is not just
                    // a better-looking trap. A customer who mistyped the key,
                    // or was issued one Owner is never going to approve, has to
                    // be able to get back to the form under their own power.
                    // Deliberately NOT disabled while busy: it does no I/O, and
                    // now that an automatic tick holds `busy` for the length of
                    // a real network round trip, gating it would put the escape
                    // hatch out of reach exactly when the network is worst.
                    OutlinedButton(
                        onClick = {
                            pendingStore.clear(); pendingRecord = null; pendingKey = ""
                            errorMessage = null; infoMessage = null; lastCheckedLabel = null
                            pollExhausted = false
                        },
                    ) { Text(tr("Use a different key")) }
                }

                // The poll that makes the "we keep checking automatically"
                // sentence TRUE. Keyed on the held key so a different
                // submission restarts it cleanly (and on pollAttempt, so a
                // manual press revives a poll that has given up); Compose
                // cancels this effect when the branch leaves composition, which
                // is what stops ticks stacking (licensing.js needs an explicit
                // stopAwaitingPoll() for the same reason). Cancellation also
                // drops the answer to a question nobody is asking any more: the
                // continuation after attemptActivation() simply never resumes.
                LaunchedEffect(pendingKey, pollAttempt) {
                    var unreachedStreak = 0
                    while (ActivationPollSchedule.shouldKeepPolling(unreachedStreak)) {
                        delay(ActivationPollSchedule.intervalMs(unreachedStreak))
                        // The concurrency guard, now bidirectional. It only ever
                        // READ `busy` before, so it stopped a tick starting
                        // during a button press but did nothing to stop a press
                        // landing mid-tick -- two overlapping /activate POSTs for
                        // the same held key, with two racing writes to the same
                        // marker and message state. `finally` matters: this
                        // coroutine is cancelled the moment the user presses
                        // "Use a different key", and a `busy` left stuck true
                        // would disable Check Now for the rest of the session.
                        if (busy) continue
                        busy = true
                        val reached = try {
                            attemptActivation(pendingKey, fromUser = false)
                        } finally {
                            busy = false
                        }
                        unreachedStreak = if (reached) 0 else unreachedStreak + 1
                    }
                    // Fell out of the loop: every one of the last
                    // MAX_CONSECUTIVE_UNREACHED attempts failed to reach the
                    // service. Stop, and let the screen say so honestly.
                    pollExhausted = true
                }
                return@Column
            }

            val (label, color) = stateLabel(state)
            AssistChip(onClick = {}, enabled = false, label = { Text(tr(label)) },
                leadingIcon = { Icon(Icons.Default.VerifiedUser, null, tint = color) })

            if (state == "NOT_CONFIGURED" || state == "ACTIVATION_REQUIRED") {
                // NOT_CONFIGURED is ambiguous by itself: the backend returns it
                // both when licensing is genuinely unconfigured for this build
                // AND when it's configured but this device has simply never
                // activated (no state record yet) -- see routes.py's
                // _not_configured_response() vs present_status(None). Only the
                // first case attaches a "detail" field, so its presence is the
                // real signal -- same fix applied to licensing.js's identical
                // message (caught via a real screenshot of the desktop gate
                // this message shares the wording with).
                val genuinelyUnconfigured = state == "NOT_CONFIGURED" && status["detail"] != null
                Text(
                    if (genuinelyUnconfigured)
                        tr("This installation is not yet connected to a licensing server. Owner licensing is not configured for this build.")
                    else tr("Enter your Aura Retail license key to activate this installation."),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )

                if (pendingRecord != null && !genuinelyUnconfigured) {
                    // Asked once more, but never blind: without this the user
                    // sees a bare key form and reasonably concludes their first
                    // submission was lost, when in fact it is sitting in
                    // Owner's approval queue. Re-submitting is genuinely safe
                    // -- Owner self-heals a repeat activation for a held
                    // installation rather than opening a second request -- and
                    // it is also the only action that can move this device
                    // forward from here.
                    Text(
                        tr("A license key from this installation is already waiting for approval from Action Aura. " +
                            "This app no longer has a copy of it, so enter the same key again to resume checking — " +
                            "re-submitting it is safe and does not create a second request."),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }

                OutlinedTextField(
                    value = licenseKeyInput, onValueChange = { licenseKeyInput = it },
                    label = { Text(tr("License key")) },
                    placeholder = { Text("AURA-RETAIL-XXXX-YYYY-ZZZZ") },
                    singleLine = true, enabled = !busy,
                    modifier = Modifier.fillMaxWidth(),
                )
                Button(
                    enabled = !busy && licenseKeyInput.isNotBlank(),
                    onClick = {
                        scope.launch {
                            errorMessage = null; infoMessage = null; busy = true
                            val key = licenseKeyInput.trim()
                            licenseKeyInput = ""
                            attemptActivation(key, fromUser = true)
                            busy = false
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    if (busy) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.onPrimary)
                    else Text(tr("Activate"))
                }
                return@Column
            }

            // Active/restricted/etc. -- status + check-in/deactivate actions.
            ElevatedCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    StatusRow(tr("Product"), status["product_code"] as? String ?: "—")
                    (status["installation_id"] as? String)?.let { StatusRow(tr("Installation"), it) }
                    (status["license_status"] as? String)?.let { StatusRow(tr("License status"), it) }
                    (status["last_successful_checkin_at"] as? String)?.let { StatusRow(tr("Last check-in"), it) }
                }
            }

            if (state in setOf("RESTRICTED", "GRACE_PERIOD", "WARNING")) {
                Text(
                    tr("Some features are limited in this state. Existing records remain fully viewable, and backup/restore/export remain available."),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (state in setOf("SUSPENDED", "REVOKED", "EXPIRED")) {
                Text(
                    tr("Commercial features are unavailable. Your existing data is safe and remains viewable; backup, restore, and export remain available."),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedButton(
                    enabled = !busy,
                    onClick = {
                        scope.launch {
                            errorMessage = null; infoMessage = null; busy = true
                            val result = try { coordinator.checkIn() } catch (e: Exception) {
                                mapOf<String, Any?>("last_attempt_reached_owner" to false)
                            }
                            infoMessage = if (result["last_attempt_reached_owner"] == true) tr("Check-in complete.") else null
                            if (result["last_attempt_reached_owner"] != true) {
                                errorMessage = tr("Could not reach the licensing service. Your current status is unchanged.")
                            }
                            refresh()
                            busy = false
                        }
                    },
                ) { Text(tr("Check Now")) }

                if (state != "DEVICE_DEACTIVATED") {
                    OutlinedButton(
                        enabled = !busy,
                        colors = ButtonDefaults.outlinedButtonColors(contentColor = MaterialTheme.colorScheme.error),
                        onClick = { confirmDeactivate = true },
                    ) { Text(tr("Deactivate This Device")) }
                }
            }
        }
    }

    if (confirmDeactivate) {
        AlertDialog(
            onDismissRequest = { confirmDeactivate = false },
            title = { Text(tr("Deactivate this device?")) },
            text = { Text(tr("You will need to reactivate with a license key to use commercial features again.")) },
            confirmButton = {
                TextButton(onClick = {
                    confirmDeactivate = false
                    scope.launch {
                        errorMessage = null; infoMessage = null; busy = true
                        val result = try { coordinator.deactivate() } catch (e: Exception) {
                            mapOf("reason_code" to "NETWORK_UNAVAILABLE")
                        }
                        if (result["result"] == "SUCCESS") infoMessage = tr("This device has been deactivated.")
                        else errorMessage = reasonText(result["reason_code"] as? String)
                        refresh()
                        busy = false
                    }
                }) { Text(tr("Deactivate")) }
            },
            dismissButton = { TextButton(onClick = { confirmDeactivate = false }) { Text(tr("Cancel")) } },
        )
    }
}

/**
 * States that mean "this device has never completed activation".
 *
 * /status deliberately returns NOT_CONFIGURED for this case too, not just for
 * a truly unconfigured build (commercial_runtime pins this on purpose in
 * test_status_before_activation_is_not_configured_shape), so the state string
 * alone cannot distinguish "unconfigured" from "configured but never
 * activated" -- BuildConfig.OWNER_LICENSING_BASE_URL is the real signal for
 * the latter, which is why AppRoot checks it before consulting this set.
 *
 * Declared once here and shared with AppRoot's boot gate: the gate decides
 * whether to route to this screen, and the awaiting-approval branch decides
 * whether to hold the user on it, so the two must react to the identical set
 * or the gate can send someone to a screen that immediately declines to show.
 */
internal val NEEDS_ACTIVATION_STATES = setOf("NOT_CONFIGURED", "ACTIVATION_REQUIRED", "ACTIVATING")

@Composable
private fun StatusRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(label, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, fontWeight = FontWeight.Medium)
    }
}

@Composable
private fun MessageBanner(text: String, isError: Boolean) {
    // Was a light-theme banner (pale pink / pale blue fill, dark text) hard-
    // coded into an app whose theme is deliberately NOT DayNight (Theme.kt) --
    // it would have rendered as a jarring light card on every other screen's
    // near-black surface. isError=false is only ever used for a completed-
    // action confirmation ("Activation successful.", "This device has been
    // deactivated.", "Check-in complete…", see the infoMessage call sites
    // below), i.e. it is a SUCCESS notice, not a neutral "info" one -- so it
    // takes Success/SuccessContainer, the same quiet-badge idiom PaymentSuccess
    // already uses, rather than a colour with no matching token here.
    // Danger/DangerContainer (7.89:1) and Success/SuccessContainer (8.37:1)
    // both clear the 4.5:1 floor by a wide margin; the original pair was never
    // measured against a dark surface because it was never meant to sit on one.
    val bg = if (isError) DangerContainer else SuccessContainer
    val fg = if (isError) Danger else Success
    Surface(color = bg, contentColor = fg, shape = MaterialTheme.shapes.small) {
        Text(text, Modifier.padding(12.dp))
    }
}
