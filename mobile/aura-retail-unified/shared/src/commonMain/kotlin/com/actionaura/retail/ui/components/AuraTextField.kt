package com.actionaura.retail.ui.components

import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import com.actionaura.retail.presentation.FormFieldState
import com.actionaura.retail.ui.theme.AuraSpacing

/**
 * M6.9 -- the one real shared text-input form component. Every M6+
 * text field (Category name/description, Branch name/address/phone,
 * and every later milestone's own text fields) uses this, never a
 * screen-specific `OutlinedTextField` copy.
 */
@Composable
fun AuraTextField(
    label: String,
    field: FormFieldState<String>,
    onValueChange: (String) -> Unit,
    modifier: Modifier = Modifier,
    singleLine: Boolean = true,
    enabled: Boolean = field.enabled,
) {
    OutlinedTextField(
        value = field.displayValue,
        onValueChange = onValueChange,
        label = { Text(label) },
        isError = field.touched && field.domainError != null,
        supportingText = {
            val error = field.domainError
            if (field.touched && error?.messageKey != null) {
                Text(error.messageKey, color = MaterialTheme.colorScheme.error)
            }
        },
        singleLine = singleLine,
        enabled = enabled,
        modifier = modifier.fillMaxWidth().padding(horizontal = AuraSpacing.md, vertical = AuraSpacing.xs),
    )
}
