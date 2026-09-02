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
        if (pos == null) loading = true   // skeleton only on first load
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

        SectionHeader(tr("Today"))
        Crossfade(targetState = loading, label = "metrics") { isLoading ->
            if (isLoading) SkeletonMetrics()
            else RetailMetrics(pos ?: RetailStats())
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
