@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.actionaura.retail.ui.screens

// Split from the source's shared DashboardScreen.kt (which held both
// RetailMetrics and ClinicMetrics behind a BuildConfig.FLAVOR check) into a
// retail-only file for this standalone project -- see
// docs/android/android-migration-plan.md. UI/business logic for the retail
// path is otherwise unchanged.

import androidx.compose.animation.Crossfade
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.actionaura.retail.net.ApiClient
import com.actionaura.retail.net.RetailStats
import com.actionaura.retail.ui.CAP_REPORTS
import com.actionaura.retail.ui.RetailSession
import com.actionaura.retail.ui.components.*
import com.actionaura.retail.ui.i18n.money
import com.actionaura.retail.ui.i18n.tr
import com.actionaura.retail.ui.theme.Info
import com.actionaura.retail.ui.theme.Success
import com.actionaura.retail.ui.theme.Warning
import java.util.Calendar

@Composable
fun DashboardScreen(onNavigate: (String) -> Unit) {
    var pos by remember { mutableStateOf<RetailStats?>(null) }
    var loading by remember { mutableStateOf(true) }
    // Never fires a fetch it already knows will be refused: measured on the
    // Mi Note 10 (2026-09-06), GET /api/sub/retail/dashboard/stats answers
    // 403 for a cashier -- deliberately, gated on retail.reports -- and the
    // old code rendered the model's zero defaults (RetailStats()) straight
    // over that refusal, so "TODAY'S SALES JD 0.000" read as a fact minutes
    // after a real JD 18.000 sale. Mirrors the desktop shell's "Ready to
    // sell" card (subsystem-retail.js) instead of painting a 403 as zero.
    val canSeeFigures = RetailSession.hasCapability(CAP_REPORTS)

    // Re-fetch whenever the Dashboard becomes visible again (e.g. after making a sale),
    // so the KPIs reflect the latest transactions/stock instead of a one-time load.
    var refreshKey by remember { mutableStateOf(0) }
    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val obs = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) refreshKey++
        }
        lifecycleOwner.lifecycle.addObserver(obs)
        onDispose { lifecycleOwner.lifecycle.removeObserver(obs) }
    }
    LaunchedEffect(refreshKey) {
        if (!canSeeFigures) {
            // No round trip to be told what the session already knows.
            pos = null
            loading = false
            return@LaunchedEffect
        }
        if (pos == null) loading = true   // skeleton only on first load
        // On failure keep whatever loaded before; a never-loaded null renders
        // the "Figures unavailable" card below, never zero defaults.
        pos = try { ApiClient.get().retailStats().data } catch (e: Exception) { pos }
        loading = false
    }

    val greeting = remember {
        when (Calendar.getInstance().get(Calendar.HOUR_OF_DAY)) {
            in 5..11 -> "Good morning"; in 12..16 -> "Good afternoon"; else -> "Good evening"
        }
    }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(18.dp)) {

        // Greeting header
        Column {
            Text(tr(greeting), style = MaterialTheme.typography.bodyLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(tr("Your store at a glance"),
                style = MaterialTheme.typography.headlineMedium,
                fontWeight = FontWeight.ExtraBold)
        }

        // Quick actions
        SectionHeader(tr("Quick actions"))
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            QuickAction(tr("New Sale"), Icons.Default.PointOfSale, Modifier.weight(1f)) { onNavigate("pos") }
            QuickAction(tr("Products"), Icons.Default.Inventory2, Modifier.weight(1f)) { onNavigate("products") }
        }

        SectionHeader(if (canSeeFigures) tr("Today") else tr("Till"))
        Crossfade(targetState = loading, label = "metrics") { isLoading ->
            val stats = pos
            when {
                isLoading -> SkeletonMetrics()
                // Deliberate 403 (retail.reports) -- a cashier has no "today"
                // figures to head, so there is nothing to render here at all.
                !canSeeFigures -> ReadyToSellCard(
                    tr("Ready to sell"),
                    tr("Sales totals and reports are limited to managers and the store owner. Open the till to start ringing sales."),
                )
                // The fetch failed and nothing was ever loaded -- never fall
                // back to RetailStats()'s zero defaults, which is exactly how
                // a refusal used to get painted as a fact.
                stats == null -> ReadyToSellCard(
                    tr("Figures unavailable"),
                    tr("Could not load today's figures. Pull to refresh or check the connection."),
                )
                else -> RetailMetrics(stats)
            }
        }
    }
}

/**
 * The honest stand-in for [RetailMetrics] when there is nothing to show it --
 * either the account cannot see figures at all, or the last fetch failed and
 * nothing was ever loaded. Same card shape both times so the layout does not
 * jump between the two reasons; only the title and body change.
 */
@Composable
private fun ReadyToSellCard(title: String, body: String) {
    TillCard(accent = MaterialTheme.colorScheme.primary) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            Text(body, style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun RetailMetrics(s: RetailStats) {
    Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
            Metric(tr("Today's Sales"), money(s.today_sales), tr("Revenue today"),
                MaterialTheme.colorScheme.primary, Modifier.weight(1f))
            Metric(tr("Transactions"), animatedInt(s.today_transactions).toString(), tr("Today"), Info, Modifier.weight(1f))
        }
        Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
            Metric(tr("Products"), animatedInt(s.total_products).toString(), tr("Catalog"), Success, Modifier.weight(1f))
            Metric(tr("Low Stock"), animatedInt(s.low_stock).toString(), tr("Need reorder"), Warning, Modifier.weight(1f))
        }
    }
}

@Composable
private fun Metric(label: String, value: String, sub: String, accent: Color, modifier: Modifier = Modifier) {
    TillCard(modifier = modifier, accent = accent) {
        Column(Modifier.padding(16.dp)) {
            Text(label.uppercase(), style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(8.dp))
            Text(value, style = MaterialTheme.typography.headlineMedium, color = accent, fontWeight = FontWeight.ExtraBold)
            Spacer(Modifier.height(2.dp))
            Text(sub, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun QuickAction(label: String, icon: ImageVector, modifier: Modifier = Modifier, onClick: () -> Unit) {
    TillCard(modifier = modifier, accent = MaterialTheme.colorScheme.primary, onClick = onClick) {
        Column(Modifier.padding(vertical = 18.dp, horizontal = 12.dp).fillMaxWidth(),
            horizontalAlignment = Alignment.CenterHorizontally) {
            Box(Modifier.size(46.dp).clip(RoundedCornerShape(14.dp))
                .background(MaterialTheme.colorScheme.primary.copy(alpha = 0.14f)),
                contentAlignment = Alignment.Center) {
                Icon(icon, null, tint = MaterialTheme.colorScheme.primary)
            }
            Spacer(Modifier.height(10.dp))
            Text(label, style = MaterialTheme.typography.labelLarge, fontWeight = FontWeight.SemiBold)
        }
    }
}
