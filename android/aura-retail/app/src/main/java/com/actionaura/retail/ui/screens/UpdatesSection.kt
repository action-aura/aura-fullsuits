package com.actionaura.retail.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.actionaura.retail.BuildConfig
import com.actionaura.retail.ui.components.SectionHeader
import com.actionaura.retail.ui.components.TillCard
import com.actionaura.retail.ui.i18n.ltrIsolate
import com.actionaura.retail.ui.i18n.tr
import com.actionaura.retail.updates.SessionApkInstaller
import com.actionaura.retail.updates.UpdateCoordinator
import com.actionaura.retail.updates.UpdateErrorCode
import com.actionaura.retail.updates.UpdateOffer
import com.actionaura.retail.updates.UpdateState
import kotlinx.coroutines.launch

/**
 * Settings -> Updates: this version, "Check for updates", and when a newer build
 * exists its version, size and notes with an "Update now" button.
 *
 * The caller shows it only to the users who see "Backup & restore" (an
 * administrator) and only when [UpdateCoordinator.isConfigured]: a build with no
 * Owner URL has nobody to ask, so the card does not exist at all rather than
 * sitting there unable to do anything.
 *
 * All the deciding happens in [com.actionaura.retail.updates.UpdateFlow]; this
 * only draws its state and forwards two button presses.
 */
@Composable
fun UpdatesSection() {
    val ctx = LocalContext.current
    val flow = remember { UpdateCoordinator.flowFor(ctx) }
    val state by flow.state.collectAsState()
    val scope = rememberCoroutineScope()

    SectionHeader(tr("Updates"))
    TillCard(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp).fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(tr("This version"), Modifier.weight(1f))
                Text(ltrIsolate(BuildConfig.VERSION_NAME), color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            when (val s = state) {
                UpdateState.Idle -> CheckButton(enabled = true) { scope.launch { flow.check() } }
                UpdateState.UpToDate -> {
                    Text(tr("You have the latest version."))
                    CheckButton(enabled = true) { scope.launch { flow.check() } }
                }
                is UpdateState.Disabled -> {
                    Text(updateErrorMessage(s.reason), color = MaterialTheme.colorScheme.onSurfaceVariant)
                    CheckButton(enabled = true) { scope.launch { flow.check() } }
                }
                is UpdateState.Error -> {
                    Text(updateErrorMessage(s.code), color = MaterialTheme.colorScheme.error)
                    CheckButton(enabled = true) { scope.launch { flow.check() } }
                }
                UpdateState.Checking -> {
                    Text(tr("Checking for updates…"))
                    LinearProgressIndicator(Modifier.fillMaxWidth())
                }
                is UpdateState.Available -> {
                    OfferDetails(s.offer)
                    Button(
                        onClick = {
                            scope.launch {
                                // One press does the whole thing: download, check,
                                // then straight into Android's own confirm dialog.
                                if (flow.download() is UpdateState.Ready) flow.install()
                            }
                        },
                        modifier = Modifier.fillMaxWidth().height(50.dp),
                    ) { Text(tr("Update now")) }
                }
                is UpdateState.Downloading -> {
                    Text(tr("Downloading the update…"))
                    if (s.total > 0) {
                        LinearProgressIndicator(
                            progress = { (s.received.toFloat() / s.total.toFloat()).coerceIn(0f, 1f) },
                            modifier = Modifier.fillMaxWidth(),
                        )
                    } else {
                        LinearProgressIndicator(Modifier.fillMaxWidth())
                    }
                }
                is UpdateState.Ready -> {
                    OfferDetails(s.offer)
                    Text(tr("The update is downloaded and checked. Press the button to install it."))
                    Button(
                        onClick = { scope.launch { flow.install() } },
                        modifier = Modifier.fillMaxWidth().height(50.dp),
                    ) { Text(tr("Install now")) }
                }
                is UpdateState.NeedsInstallPermission -> {
                    Text(tr("Android needs your permission before this app can install updates. Allow it in Settings, then come back and press Install now."))
                    OutlinedButton(
                        onClick = { ctx.startActivity(SessionApkInstaller.unknownAppSourcesIntent(ctx)) },
                        modifier = Modifier.fillMaxWidth().height(50.dp),
                    ) { Text(tr("Open Settings")) }
                    Button(
                        onClick = { scope.launch { flow.install() } },
                        modifier = Modifier.fillMaxWidth().height(50.dp),
                    ) { Text(tr("Install now")) }
                }
                UpdateState.Installing -> {
                    Text(tr("Installing… follow the Android prompt on the screen."))
                    LinearProgressIndicator(Modifier.fillMaxWidth())
                }
            }
        }
    }
}

@Composable
private fun CheckButton(enabled: Boolean, onClick: () -> Unit) {
    OutlinedButton(onClick = onClick, enabled = enabled, modifier = Modifier.fillMaxWidth().height(50.dp)) {
        Text(tr("Check for updates"))
    }
}

@Composable
private fun OfferDetails(offer: UpdateOffer) {
    Text(tr("A new version is available: %s").format(ltrIsolate(offer.version)))
    Text(
        tr("Download size: %s MB").format("%.1f".format(offer.sizeBytes / (1024.0 * 1024.0))),
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    if (offer.updateRequired) Text(tr("This update is required."), color = MaterialTheme.colorScheme.error)
    // Owner's words, shown exactly as written (not translated, not edited).
    offer.releaseNotes?.takeIf { it.isNotBlank() }?.let { Text(it) }
}

/**
 * Plain words for every [UpdateErrorCode]. One branch per code, spelled with the
 * constant, so UpdatesWiringContractTest can fail the build when a new code is
 * added without a sentence here (it would otherwise fall through to the generic
 * line and hide what actually went wrong).
 */
internal fun updateErrorMessage(code: String): String = when (code) {
    UpdateErrorCode.NOT_CONFIGURED -> tr("Updates are not set up on this build.")
    UpdateErrorCode.NOT_ACTIVATED -> tr("Activate this device first, then updates can be checked.")
    UpdateErrorCode.OWNER_UNREACHABLE -> tr("Couldn't reach the update server. Check the internet connection and try again.")
    UpdateErrorCode.LOCAL_UNAVAILABLE -> tr("The app couldn't check for updates just now. Try again in a moment.")
    UpdateErrorCode.DESCRIPTOR_UNTRUSTED -> tr("This update could not be proven genuine, so it was not used.")
    UpdateErrorCode.DESCRIPTOR_MISMATCH -> tr("The update details didn't match this device, so they were not used.")
    UpdateErrorCode.DESCRIPTOR_INVALID -> tr("The update details were incomplete or wrong, so they were not used.")
    UpdateErrorCode.OWNER_REFUSED -> tr("The update server says this device can't get updates right now. Check the licence.")
    UpdateErrorCode.AUTHORIZE_REFUSED -> tr("The update server didn't allow the download. Try again later.")
    UpdateErrorCode.DOWNLOAD_FAILED -> tr("The download failed. Check the internet connection and try again.")
    UpdateErrorCode.DOWNLOAD_CORRUPT -> tr("The downloaded file was damaged, so it was deleted. Try again.")
    UpdateErrorCode.SIGNATURE_MISMATCH -> tr("This update was not signed by Action Aura, so it was not installed.")
    UpdateErrorCode.WRONG_PACKAGE -> tr("This update is for a different app, so it was not installed.")
    UpdateErrorCode.NOT_NEWER -> tr("This update is not newer than the version you already have.")
    UpdateErrorCode.INSTALLER_TAMPERED -> tr("The downloaded file changed before it could be installed, so it was deleted. Try again.")
    UpdateErrorCode.INSTALL_FAILED -> tr("Android couldn't finish the install. Try again.")
    else -> tr("Something went wrong with the update. Try again.")
}
