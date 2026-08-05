package com.actionaura.retail.ui.category

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Star
import androidx.compose.material3.Button
import androidx.compose.material3.ListItem
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavHostController
import com.actionaura.retail.di.LocalAuraAppContainer
import com.actionaura.retail.presentation.LoadState
import com.actionaura.retail.ui.components.AuraEmptyState
import com.actionaura.retail.ui.components.AuraLoadingState
import com.actionaura.retail.ui.components.AuraScaffold
import com.actionaura.retail.ui.components.AuraSearchBar
import com.actionaura.retail.ui.components.FeatureUnavailableScreen
import com.actionaura.retail.ui.components.UnavailableFeatureInfo
import com.actionaura.retail.ui.navigation.AuraRoute
import com.actionaura.retail.ui.theme.AuraSpacing

/**
 * M6.16 -- the real Category list screen. Uses ONLY
 * `CategoryListViewModel` (which itself uses only real M5.2/M5.3 use
 * cases) -- never queries SQLDelight directly, never renders hard-
 * coded sample data.
 */
@Composable
fun CategoryListScreen(navController: NavHostController) {
    val container = LocalAuraAppContainer.current
    if (container == null) {
        FeatureUnavailableScreen(UnavailableFeatureInfo("Categories", "Database not yet initialized"))
        return
    }
    val viewModel: CategoryListViewModel = viewModel { CategoryListViewModel(container) }
    val state by viewModel.state.collectAsState()
    val snackbarHostState = remember { SnackbarHostState() }

    LaunchedEffect(Unit) { viewModel.load() }
    LaunchedEffect(viewModel) {
        viewModel.effects.collect { effect ->
            when (effect) {
                is CategoryListEffect.ShowMessage -> snackbarHostState.showSnackbar(effect.message.key)
            }
        }
    }

    AuraScaffold(title = "Categories", snackbarHostState = snackbarHostState) { padding ->
        Column(modifier = Modifier.padding(padding)) {
            AuraSearchBar(state.searchState, viewModel::onSearchQueryChange, placeholder = "Search categories")
            Button(onClick = { navController.navigate(AuraRoute.CategoryCreate) }, modifier = Modifier.padding(AuraSpacing.md)) {
                Text("Add category")
            }
            when {
                state.loadState is LoadState.Loading && state.categories.isEmpty() -> AuraLoadingState()
                state.visibleCategories.isEmpty() -> AuraEmptyState(
                    icon = Icons.Filled.Star,
                    title = "No categories yet",
                )
                else -> LazyColumn {
                    items(state.visibleCategories) { category ->
                        ListItem(
                            headlineContent = { Text(category.name) },
                            supportingContent = category.description?.let { { Text(it) } },
                            trailingContent = {
                                Button(onClick = { viewModel.onArchive(category.id) }) { Text("Archive") }
                            },
                        )
                    }
                }
            }
        }
    }
}
