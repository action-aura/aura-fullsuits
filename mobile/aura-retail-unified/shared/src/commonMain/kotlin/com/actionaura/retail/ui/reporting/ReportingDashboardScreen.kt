package com.actionaura.retail.ui.reporting

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.lifecycle.viewmodel.compose.viewModel
import com.actionaura.retail.di.LocalAuraAppContainer
import com.actionaura.retail.presentation.LoadState
import com.actionaura.retail.ui.components.AuraCard
import com.actionaura.retail.ui.components.AuraLoadingState
import com.actionaura.retail.ui.components.AuraScaffold
import com.actionaura.retail.ui.components.AuraSection
import com.actionaura.retail.ui.components.FeatureUnavailableScreen
import com.actionaura.retail.ui.components.UnavailableFeatureInfo
import com.actionaura.retail.ui.format.formatMoney
import com.actionaura.retail.ui.format.formatQuantity

/**
 * M6.18 -- the real Reporting/Dashboard screen. Every displayed total
 * comes from the real, exact `Money`/`Quantity` via `formatMoney`/
 * `formatQuantity` (M6.10) -- never a re-derived or rounded value.
 * Real, disclosed scope: cards and lists only, no chart library
 * integration this milestone (`reporting-ui-vertical-slice.md`).
 */
@Composable
fun ReportingDashboardScreen() {
    val container = LocalAuraAppContainer.current
    if (container == null) {
        FeatureUnavailableScreen(UnavailableFeatureInfo("Dashboard", "Database not yet initialized"))
        return
    }
    val viewModel: ReportingDashboardViewModel = viewModel { ReportingDashboardViewModel(container) }
    val state by viewModel.state.collectAsState()
    val snackbarHostState = remember { SnackbarHostState() }

    LaunchedEffect(Unit) { viewModel.load() }
    LaunchedEffect(viewModel) {
        viewModel.effects.collect { effect ->
            when (effect) {
                is ReportingDashboardEffect.ShowMessage -> snackbarHostState.showSnackbar(effect.message.key)
            }
        }
    }

    AuraScaffold(title = "Dashboard", snackbarHostState = snackbarHostState) { padding ->
        val snapshot = state.snapshot
        if (state.loadState is LoadState.Loading && snapshot == null) {
            AuraLoadingState(modifier = Modifier.padding(padding))
            return@AuraScaffold
        }
        if (snapshot == null) return@AuraScaffold

        Column(modifier = Modifier.padding(padding)) {
            AuraSection(title = "Today") {
                AuraCard {
                    Text("Gross: ${formatMoney(snapshot.today.grossSales, snapshot.currency)}")
                    Text("Net: ${formatMoney(snapshot.today.netSales, snapshot.currency)}")
                    Text("Transactions: ${snapshot.today.transactionCount}")
                }
            }
            AuraSection(title = "Last 7 days") {
                AuraCard {
                    Text("Gross: ${formatMoney(snapshot.selectedPeriod.grossSales, snapshot.currency)}")
                    Text("Net: ${formatMoney(snapshot.selectedPeriod.netSales, snapshot.currency)}")
                    Text("Returns: ${formatMoney(snapshot.selectedPeriod.confirmedReturns, snapshot.currency)}")
                }
            }
            if (snapshot.lowStockCount > 0) {
                AuraSection(title = "Low stock (${snapshot.lowStockCount})") {
                    snapshot.lowStockPreview.forEach { item ->
                        AuraCard { Text("${item.productName}: ${formatQuantity(item.totalOnHand)} (reorder at ${item.reorderLevel})") }
                    }
                }
            }
            if (snapshot.salesTrend.buckets.isNotEmpty()) {
                AuraSection(title = "Sales trend (last 7 days)") {
                    snapshot.salesTrend.buckets.forEach { bucket ->
                        AuraCard { Text("${bucket.bucketLabelKey}: ${formatMoney(bucket.summary.netSales, snapshot.currency)}") }
                    }
                }
            }
            if (snapshot.topProductsByNetRevenue.isNotEmpty()) {
                AuraSection(title = "Top products by revenue") {
                    snapshot.topProductsByNetRevenue.forEach { metric ->
                        AuraCard { Text("${metric.productName}: ${formatMoney(metric.netRevenue, metric.currency)}") }
                    }
                }
            }
            if (snapshot.dataQualityIssues.isNotEmpty()) {
                AuraSection(title = "Data quality warnings") {
                    AuraCard { Text("${snapshot.dataQualityIssues.size} real issue(s) detected -- see diagnostics") }
                }
            }
        }
    }
}
