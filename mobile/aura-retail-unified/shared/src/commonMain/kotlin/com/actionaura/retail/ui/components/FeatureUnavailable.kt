package com.actionaura.retail.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Build
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import com.actionaura.retail.ui.theme.AuraDimensions
import com.actionaura.retail.ui.theme.AuraSpacing

/**
 * M6.7/M6.20 -- the real, explicitly-typed "unavailable" contract the
 * checkpoint itself requires ("every interactive M6 vertical slice must
 * use a real shared use case or an explicitly typed unavailable-state
 * contract"). Every `AuraRoute` not yet backed by a real M6 vertical
 * slice renders this, citing the REAL owning milestone from
 * `mobile-screen-route-matrix.md` -- never hard-coded sample data, never
 * a silently blank screen.
 */
data class UnavailableFeatureInfo(
    val titleKey: String,
    val ownedByMilestone: String,
)

@Composable
fun FeatureUnavailableScreen(info: UnavailableFeatureInfo, modifier: Modifier = Modifier) {
    Column(
        modifier = modifier.fillMaxSize().padding(AuraSpacing.lg),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Icon(Icons.Filled.Build, contentDescription = null, modifier = Modifier.padding(bottom = AuraSpacing.md))
        Text(info.titleKey, style = MaterialTheme.typography.titleMedium)
        androidx.compose.foundation.layout.Spacer(Modifier.padding(AuraSpacing.xs))
        Text(
            "Planned: ${info.ownedByMilestone}",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}
