@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.actionaura.clinic.ui.screens

// Split from the source's shared DashboardScreen.kt (which held both
// RetailMetrics and ClinicMetrics behind a BuildConfig.FLAVOR check) into a
// clinic-only file for this standalone project -- see
// docs/android/android-migration-plan.md. UI/business logic for the clinic
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
import com.actionaura.clinic.net.ApiClient
import com.actionaura.clinic.net.ClinicStats
import com.actionaura.clinic.ui.components.*
import com.actionaura.clinic.ui.i18n.money
import com.actionaura.clinic.ui.i18n.tr
import com.actionaura.clinic.ui.theme.Info
import com.actionaura.clinic.ui.theme.Success
import com.actionaura.clinic.ui.theme.Warning
import java.util.Calendar

@Composable
fun DashboardScreen(onNavigate: (String) -> Unit) {
    var clinic by remember { mutableStateOf<ClinicStats?>(null) }
    var loading by remember { mutableStateOf(true) }

    // Re-fetch whenever the Dashboard becomes visible again, so the KPIs
    // reflect the latest patients/appointments/invoices instead of a
    // one-time load.
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
        if (clinic == null) loading = true   // skeleton only on first load
        clinic = try { ApiClient.get().clinicStats().data } catch (e: Exception) { clinic }
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
            Text(tr("Your clinic at a glance"),
                style = MaterialTheme.typography.headlineMedium.copy(brush = auroraBrush()),
                fontWeight = FontWeight.ExtraBold)
        }

        // Quick actions
        SectionHeader(tr("Quick actions"))
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            QuickAction(tr("Add Patient"), Icons.Default.PersonAdd, Modifier.weight(1f)) { onNavigate("patients") }
            QuickAction(tr("Book"), Icons.Default.EventAvailable, Modifier.weight(1f)) { onNavigate("appointments") }
            QuickAction(tr("Invoice"), Icons.Default.ReceiptLong, Modifier.weight(1f)) { onNavigate("billing") }
        }

        SectionHeader(tr("Today"))
        Crossfade(targetState = loading, label = "metrics") { isLoading ->
            if (isLoading) SkeletonMetrics()
            else ClinicMetrics(clinic ?: ClinicStats())
        }
    }
}

@Composable
private fun ClinicMetrics(s: ClinicStats) {
    Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
            Metric(tr("Appointments"), animatedInt(s.today_appointments).toString(), tr("Scheduled today"),
                MaterialTheme.colorScheme.primary, Modifier.weight(1f))
            Metric(tr("Waiting"), animatedInt(s.waiting).toString(), tr("Checked in"), Warning, Modifier.weight(1f))
        }
        Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
            Metric(tr("Active Visits"), animatedInt(s.active_visits).toString(), tr("In consultation"), Info, Modifier.weight(1f))
            Metric(tr("Revenue"), money(s.today_revenue), tr("Collected today"), Success, Modifier.weight(1f))
        }
        Metric(tr("Total Patients"), animatedInt(s.total_patients).toString(), tr("All time"),
            MaterialTheme.colorScheme.primary, Modifier.fillMaxWidth())
    }
}

@Composable
private fun Metric(label: String, value: String, sub: String, accent: Color, modifier: Modifier = Modifier) {
    GlowCard(modifier = modifier, glow = accent) {
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
    GlowCard(modifier = modifier, glow = MaterialTheme.colorScheme.primary, onClick = onClick) {
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
