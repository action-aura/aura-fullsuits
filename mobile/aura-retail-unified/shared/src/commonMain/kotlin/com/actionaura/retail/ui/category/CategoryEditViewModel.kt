package com.actionaura.retail.ui.category

import com.actionaura.retail.data.DomainResult
import com.actionaura.retail.di.AuraAppContainer
import com.actionaura.retail.presentation.AuraViewModel
import com.actionaura.retail.presentation.FormFieldState
import com.actionaura.retail.presentation.SubmissionState
import com.actionaura.retail.presentation.UiEffect
import com.actionaura.retail.presentation.UiFieldError
import com.actionaura.retail.presentation.toUiMessage

/**
 * M6.16 -- real Category create form, using the real
 * `CreateCategoryUseCase` (real Arabic/Unicode-normalized duplicate-
 * name detection, M5.3). `CategoryRepository` has no update method
 * (M5.1's own real, confirmed scope), so edit-existing-category is a
 * real, disclosed gap this milestone -- only create is wired
 * (`category-ui-vertical-slice.md`).
 */
data class CategoryEditUiState(
    val name: FormFieldState<String> = FormFieldState.empty(required = true),
    val description: FormFieldState<String> = FormFieldState.empty(required = false),
    val submissionState: SubmissionState = SubmissionState.Idle,
) {
    val canSubmit: Boolean get() = name.isValid && description.isValid && submissionState != SubmissionState.Submitting
}

sealed interface CategoryEditEffect : UiEffect {
    data object SavedSuccessfully : CategoryEditEffect
}

class CategoryEditViewModel(
    private val container: AuraAppContainer,
    private val companyId: Long = 1L,
    dispatcher: kotlinx.coroutines.CoroutineDispatcher = kotlinx.coroutines.Dispatchers.Default,
) : AuraViewModel<CategoryEditUiState, CategoryEditEffect>(CategoryEditUiState(), dispatcher) {

    fun onNameChange(text: String) = setState { it.copy(name = it.name.copy(value = text.ifEmpty { null }, displayValue = text, touched = true, domainError = null)) }
    fun onDescriptionChange(text: String) = setState { it.copy(description = it.description.copy(value = text.ifEmpty { null }, displayValue = text, touched = true)) }

    fun onSave() = launchOnDefault {
        val name = currentState.name.value
        if (name.isNullOrBlank()) {
            setState { it.copy(name = it.name.copy(touched = true, domainError = UiFieldError("name", "error.required"))) }
            return@launchOnDefault
        }
        setState { it.copy(submissionState = SubmissionState.Submitting) }
        val result = container.createCategoryUseCase.execute(companyId, name, currentState.description.value, nowEpochMillis())
        when (result) {
            is DomainResult.Success -> {
                setState { it.copy(submissionState = SubmissionState.Success) }
                sendEffect(CategoryEditEffect.SavedSuccessfully)
            }
            is DomainResult.Failure -> {
                val message = result.error.toUiMessage()
                setState {
                    it.copy(
                        submissionState = SubmissionState.Idle,
                        name = it.name.copy(domainError = UiFieldError("name", message.key)),
                    )
                }
            }
        }
    }
}
