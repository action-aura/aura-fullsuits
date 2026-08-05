package com.actionaura.retail.ui.reporting

import com.actionaura.retail.di.AuraAppContainer
import com.actionaura.retail.presentation.AuraViewModel
import com.actionaura.retail.presentation.LoadState
import com.actionaura.retail.presentation.UiEffect
import com.actionaura.retail.presentation.UiMessage
import com.actionaura.retail.reporting.DashboardSnapshot
import com.actionaura.retail.reporting.ReportPeriodFactory
import com.actionaura.retail.reporting.ReportScope
import com.actionaura.retail.ui.category.nowEpochMillis
import kotlinx.datetime.TimeZone

/**
 * M6.18 -- the real Reporting/Dashboard ViewModel, built entirely on
 * the real, already-implemented M5.6/M5.7 `DashboardRepository.getDashboard`
 * (one real call composes today/selected-period summaries, low-stock
 * preview, top products by quantity/revenue, and a real sales trend --
 * `dashboard-authority-contract.md`'s own "no duplicated formulas"
 * guarantee, reused here, not re-derived in the UI layer).
 *
 * Real, disclosed scope: `businessTimeZone` is hardcoded to `TimeZone.UTC`
 * this milestone -- no real business-timezone SETTING UI exists yet
 * (`ReportPeriodFactory`'s own real, injected-timezone design already
 * supports a real per-company value; only the UI to configure one is
 * `NOT_IN_M6`). "Selected period" is a real, fixed last-7-days window --
 * no real date-range picker UI exists yet either, both disclosed here
 * rather than silently presented as configurable.
 */
data class ReportingDashboardUiState(
    val snapshot: DashboardSnapshot? = null,
    val loadState: LoadState = LoadState.Idle,
)

sealed interface ReportingDashboardEffect : UiEffect {
    data class ShowMessage(val message: UiMessage) : ReportingDashboardEffect
}

class ReportingDashboardViewModel(
    private val container: AuraAppContainer,
    private val companyId: Long = 1L,
    dispatcher: kotlinx.coroutines.CoroutineDispatcher = kotlinx.coroutines.Dispatchers.Default,
) : AuraViewModel<ReportingDashboardUiState, ReportingDashboardEffect>(ReportingDashboardUiState(), dispatcher) {

    fun load() = launchOnDefault {
        setState { it.copy(loadState = LoadState.Loading) }
        val now = nowEpochMillis()
        val businessTimeZone = TimeZone.UTC
        val scope = ReportScope(companyId)
        val todayPeriod = ReportPeriodFactory.today(businessTimeZone, now)
        val selectedPeriod = ReportPeriodFactory.customRange(now - SEVEN_DAYS_MILLIS, now)
        val trendBuckets = ReportPeriodFactory.dailyBuckets(businessTimeZone, selectedPeriod.startInclusiveEpochMillis, selectedPeriod.endExclusiveEpochMillis)
            .map { it to it.startInclusiveEpochMillis.toString() }

        val snapshot = container.dashboardRepository.getDashboard(
            scope = scope,
            todayPeriod = todayPeriod,
            selectedPeriod = selectedPeriod,
            trendBuckets = trendBuckets,
            topProductsLimit = 10L,
            lowStockPreviewLimit = 10L,
            nowEpochMillis = now,
        )
        setState { it.copy(snapshot = snapshot, loadState = LoadState.Success) }
    }

    private companion object {
        const val SEVEN_DAYS_MILLIS = 7L * 24 * 60 * 60 * 1000
    }
}
