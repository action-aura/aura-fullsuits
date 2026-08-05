package com.actionaura.retail.ui.category

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavHostController
import com.actionaura.retail.di.LocalAuraAppContainer
import com.actionaura.retail.presentation.SubmissionState
import com.actionaura.retail.ui.components.AuraScaffold
import com.actionaura.retail.ui.components.AuraTextField
import com.actionaura.retail.ui.components.FeatureUnavailableScreen
import com.actionaura.retail.ui.components.UnavailableFeatureInfo
import com.actionaura.retail.ui.theme.AuraSpacing

/** M6.16 -- real Category create form (see `CategoryEditViewModel`'s own KDoc for the real, disclosed "create only, no edit-existing" scope). */
@Composable
fun CategoryEditScreen(navController: NavHostController) {
    val container = LocalAuraAppContainer.current
    if (container == null) {
        FeatureUnavailableScreen(UnavailableFeatureInfo("Add category", "Database not yet initialized"))
        return
    }
    val viewModel: CategoryEditViewModel = viewModel { CategoryEditViewModel(container) }
    val state by viewModel.state.collectAsState()

    LaunchedEffect(viewModel) {
        viewModel.effects.collect { effect ->
            when (effect) {
                CategoryEditEffect.SavedSuccessfully -> navController.popBackStack()
            }
        }
    }

    AuraScaffold(title = "Add category", onNavigateBack = { navController.popBackStack() }) { padding ->
        Column(modifier = Modifier.padding(padding)) {
            AuraTextField(label = "Category name", field = state.name, onValueChange = viewModel::onNameChange)
            AuraTextField(label = "Description", field = state.description, onValueChange = viewModel::onDescriptionChange)
            Button(
                onClick = viewModel::onSave,
                enabled = state.canSubmit,
                modifier = Modifier.padding(AuraSpacing.md),
            ) {
                Text(if (state.submissionState == SubmissionState.Submitting) "Saving..." else "Save")
            }
        }
    }
}
