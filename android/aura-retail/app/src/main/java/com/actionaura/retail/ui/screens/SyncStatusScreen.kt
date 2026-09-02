package com.actionaura.retail.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.actionaura.retail.sync.SYNC_DEGRADED_THRESHOLD
import com.actionaura.retail.sync.SyncCoordinator
import com.actionaura.retail.sync.SyncHalfHealth
import com.actionaura.retail.sync.SyncHealth
import com.actionaura.retail.sync.SyncTier
import com.actionaura.retail.sync.classifySync
import com.actionaura.retail.sync.formatRelative
import com.actionaura.retail.sync.mostRecentSuccessMillis
import com.actionaura.retail.ui.components.SectionHeader
import com.actionaura.retail.ui.components.TillCard
import com.actionaura.retail.ui.i18n.tr
import kotlinx.coroutines.delay

// ══════════════════════════════════════════════════════════════════════════════
//  SYNC STATUS -- the UI `SyncCoordinator.health()` never had a reader for
// ══════════════════════════════════════════════════════════════════════════════
//
// `SyncCoordinator.health()`'s own doc comment used to say: "No UI consumes
// this yet ... this exists so a failure is inspectable rather than invisible,
// and is the seam any future UI would read." This screen is that reader.
//
// This screen reads [SyncCoordinator.health] -- NEVER
// `/api/sub/retail/sync/health` -- because on Android the two roles are
// split differently than on desktop: Kotlin's SyncCoordinator makes the real
// signed HTTP calls to Owner, while the embedded Python backend's own
// SyncService is constructed with `client_factory=None` and only serves the
// narrow internal seam SyncCoordinator itself calls (see SyncCoordinator.kt's
// class doc comment for the full split). The HTTP route is served on
// Android, but it describes a push/pull loop that never actually runs on
// this platform -- reading it here would show a service that is permanently
// idle, not this device's real sync health.

/**
 * The user-facing sync status screen (Device section of More). Polls
 * [SyncCoordinator.health] every 2s -- a cheap in-memory read, not a network
 * call, so polling costs nothing measurable.
 */
@Composable
fun SyncStatusScreen() {
    var health by remember { mutableStateOf(SyncCoordinator.health()) }
    var now by remember { mutableStateOf(System.currentTimeMillis()) }

    LaunchedEffect(Unit) {
        while (true) {
            health = SyncCoordinator.health()
            now = System.currentTimeMillis()
            delay(2000)
        }
    }

    val tier = classifySync(health)

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        SyncHeadlineCard(tier, health, now)
        // Pending count is meaningless when there is no relay configured at
        // all -- shown for every other tier, including NEVER_SYNCED, where a
        // nonzero count is itself informative (things are queued, waiting).
        if (tier != SyncTier.NOT_CONFIGURED) SyncPendingCard(health.pendingCount)
        SectionHeader(tr("Detail"))
        SyncBreakdownCard(health, now)
    }
}

@Composable
private fun SyncHeadlineCard(tier: SyncTier, health: SyncHealth, now: Long) {
    val title: String
    val body: String
    val titleColor: androidx.compose.ui.graphics.Color
    when (tier) {
        SyncTier.NOT_CONFIGURED -> {
            title = tr("Sync is not set up")
            body = tr("This build has no relay address, so this device never sends or receives. Other devices will not see its sales.")
            titleColor = MaterialTheme.colorScheme.onSurfaceVariant
        }
        SyncTier.NEVER_SYNCED -> {
            title = tr("Waiting for first sync")
            body = tr("Set up, but this device has not reached the relay yet.")
            titleColor = MaterialTheme.colorScheme.onSurface
        }
        SyncTier.FAILING -> {
            // Whichever half tripped the FAILING threshold names its own
            // reason and count -- if both did, push is shown first (the
            // outbound half is what an owner usually asks about first: "did
            // my sale go through").
            val failingHalf = if (health.push.consecutiveFailures >= SYNC_DEGRADED_THRESHOLD) health.push else health.pull
            // lastFailureReason is a raw diagnostic code (e.g. from
            // SyncRelayClientError.reasonCode), not translated -- same
            // convention as StartupErrorScreen's diagnostic: it is the
            // machine detail support needs verbatim, and translating it
            // would make it useless to whoever has to read it back.
            val reasonSuffix = failingHalf.lastFailureReason?.let { " $it" } ?: ""
            title = tr("Sync is failing")
            body = tr("This device is not reaching your other devices.") + reasonSuffix +
                " — ${failingHalf.consecutiveFailures} " + tr("consecutive failures")
            titleColor = MaterialTheme.colorScheme.error
        }
        SyncTier.CALM -> {
            // mostRecentSuccessMillis(health) cannot be null here -- CALM is
            // only reached when classifySync() has already confirmed a
            // success exists -- but the fallback is written defensively
            // rather than force-unwrapped, and deliberately matches the same
            // bare "just now" formatRelative() itself would return for a
            // near-zero delta, so it never needs its own translation entry.
            val last = mostRecentSuccessMillis(health)
            val whenText = last?.let { formatRelative(now - it) } ?: "just now"
            title = tr("Sync is working")
            body = tr("Last synced") + " " + whenText
            titleColor = MaterialTheme.colorScheme.onSurface
        }
    }

    TillCard(
        Modifier.fillMaxWidth(),
        accent = if (tier == SyncTier.FAILING) MaterialTheme.colorScheme.error else null,
    ) {
        Column(Modifier.padding(16.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold, color = titleColor)
            Spacer(Modifier.height(6.dp))
            Text(body, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun SyncPendingCard(pendingCount: Int) {
    TillCard(Modifier.fillMaxWidth()) {
        Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(pendingCount.toString(), style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.ExtraBold)
            Spacer(Modifier.width(8.dp))
            Text(tr("waiting to send"), color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun SyncBreakdownCard(health: SyncHealth, now: Long) {
    TillCard(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            SyncHalfRow(tr("Sending"), health.push, now)
            HorizontalDivider()
            SyncHalfRow(tr("Receiving"), health.pull, now)
        }
    }
}

@Composable
private fun SyncHalfRow(label: String, half: SyncHalfHealth, now: Long) {
    Column {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(label, Modifier.weight(1f), fontWeight = FontWeight.Bold)
            Text(
                if (half.healthy) tr("Healthy") else tr("Failing"),
                color = if (half.healthy) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.error,
                fontWeight = FontWeight.Medium,
            )
        }
        val lastText = half.lastSuccessAtMillis?.let { formatRelative(now - it) } ?: tr("never")
        Text(
            tr("Last success") + " " + lastText,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        // Not translated -- see the FAILING headline above: a raw diagnostic
        // code, shown verbatim.
        half.lastFailureReason?.let {
            Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
        }
    }
}
