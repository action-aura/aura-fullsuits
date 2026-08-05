package com.actionaura.retail.ui.importing

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedTextField
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
import com.actionaura.retail.ui.components.AuraCard
import com.actionaura.retail.ui.components.AuraScaffold
import com.actionaura.retail.ui.components.FeatureUnavailableScreen
import com.actionaura.retail.ui.components.UnavailableFeatureInfo
import com.actionaura.retail.ui.theme.AuraSpacing

/** M6.19 -- real Import Center screen (Categories, paste-based input -- see `ImportCategoriesViewModel`'s own KDoc for the full real, disclosed scope). */
@Composable
fun ImportCategoriesScreen() {
    val container = LocalAuraAppContainer.current
    if (container == null) {
        FeatureUnavailableScreen(UnavailableFeatureInfo("Import Center", "Database not yet initialized"))
        return
    }
    val viewModel: ImportCategoriesViewModel = viewModel { ImportCategoriesViewModel(container) }
    val state by viewModel.state.collectAsState()
    val snackbarHostState = remember { SnackbarHostState() }

    LaunchedEffect(viewModel) {
        viewModel.effects.collect { effect ->
            when (effect) {
                is ImportCategoriesEffect.ShowMessage -> snackbarHostState.showSnackbar(effect.message.key)
            }
        }
    }

    AuraScaffold(title = "Import Categories", snackbarHostState = snackbarHostState) { padding ->
        Column(modifier = Modifier.padding(padding).padding(AuraSpacing.md)) {
            when (state.step) {
                ImportStep.PasteInput, ImportStep.Previewing -> {
                    Text("Paste CSV content (header: name,description)")
                    OutlinedTextField(
                        value = state.pastedText,
                        onValueChange = viewModel::onTextChange,
                        modifier = Modifier.padding(top = AuraSpacing.sm),
                    )
                    Button(onClick = viewModel::onPreview, modifier = Modifier.padding(top = AuraSpacing.md)) {
                        Text("Preview")
                    }
                }
                ImportStep.DryRunReady -> {
                    val dryRun = state.dryRun
                    AuraCard {
                        Text("Real dry-run plan")
                        Text("Total rows: ${dryRun?.totalRows}")
                        Text("Planned inserts: ${dryRun?.plannedInserts}")
                        Text("Commit eligible: ${dryRun?.commitEligible}")
                    }
                    state.errorMessage?.let { Text(it.key) }
                    Button(
                        onClick = viewModel::onCommit,
                        enabled = dryRun?.commitEligible == true,
                        modifier = Modifier.padding(top = AuraSpacing.md),
                    ) { Text("Commit") }
                }
                ImportStep.Committing -> Text("Committing...")
                ImportStep.Result -> {
                    val result = state.commitResult
                    AuraCard {
                        Text("Real, immutable result")
                        Text("Outcome: ${result?.outcome}")
                        Text("Inserted: ${result?.insertedCounts}")
                        Text("Skipped: ${result?.skippedCount}")
                    }
                }
            }
        }
    }
}
