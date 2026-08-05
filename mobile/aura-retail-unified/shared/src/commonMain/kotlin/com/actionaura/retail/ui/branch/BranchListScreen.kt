package com.actionaura.retail.ui.branch

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Home
import androidx.compose.material3.Button
import androidx.compose.material3.ListItem
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavHostController
import com.actionaura.retail.di.LocalAuraAppContainer
import com.actionaura.retail.presentation.LoadState
import com.actionaura.retail.ui.components.AuraEmptyState
import com.actionaura.retail.ui.components.AuraLoadingState
import com.actionaura.retail.ui.components.AuraScaffold
import com.actionaura.retail.ui.components.FeatureUnavailableScreen
import com.actionaura.retail.ui.components.UnavailableFeatureInfo
import com.actionaura.retail.ui.theme.AuraSpacing

/**
 * M6.17 -- real Branch list + inline create screen. Real "select
 * current branch" radio, real archive/reactivate actions, all backed
 * by the real M5.4 use cases via `BranchListViewModel`.
 */
@Composable
fun BranchListScreen(navController: NavHostController) {
    val container = LocalAuraAppContainer.current
    if (container == null) {
        FeatureUnavailableScreen(UnavailableFeatureInfo("Branches", "Database not yet initialized"))
        return
    }
    val viewModel: BranchListViewModel = viewModel { BranchListViewModel(container) }
    val state by viewModel.state.collectAsState()
    val snackbarHostState = remember { SnackbarHostState() }
    var newBranchName by remember { mutableStateOf("") }

    LaunchedEffect(Unit) { viewModel.load() }
    LaunchedEffect(viewModel) {
        viewModel.effects.collect { effect ->
            when (effect) {
                is BranchListEffect.ShowMessage -> snackbarHostState.showSnackbar(effect.message.key)
            }
        }
    }

    AuraScaffold(title = "Branches", snackbarHostState = snackbarHostState) { padding ->
        Column(modifier = Modifier.padding(padding)) {
            Row(modifier = Modifier.padding(AuraSpacing.md)) {
                OutlinedTextField(value = newBranchName, onValueChange = { newBranchName = it }, label = { androidx.compose.material3.Text("New branch name") })
                Button(onClick = { viewModel.onCreate(newBranchName); newBranchName = "" }, modifier = Modifier.padding(start = AuraSpacing.sm)) {
                    Text("Add")
                }
            }
            when {
                state.loadState is LoadState.Loading && state.branches.isEmpty() -> AuraLoadingState()
                state.branches.isEmpty() -> AuraEmptyState(
                    icon = Icons.Filled.Home,
                    title = "No branches yet",
                )
                else -> LazyColumn {
                    items(state.branches) { branch ->
                        ListItem(
                            headlineContent = { Text(branch.name) },
                            leadingContent = {
                                RadioButton(selected = branch.id == state.currentBranchId, onClick = { viewModel.onSelectCurrent(branch.id) })
                            },
                            trailingContent = {
                                Button(onClick = { viewModel.onArchive(branch.id) }) { Text("Archive") }
                            },
                        )
                    }
                }
            }
        }
    }
}
