package com.actionaura.retail.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import com.actionaura.retail.presentation.UiMessage
import com.actionaura.retail.ui.theme.AuraSpacing

/**
 * M6.8 -- the shared reusable screen scaffold/component catalog. Every
 * M6+ vertical slice (Category/Branch/Reporting/Import, and every later
 * milestone's screens) builds on these, never a screen-specific copy of
 * the same structure (`common-component-catalog.md`'s own explicit
 * rule).
 */

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AuraTopAppBar(title: String, onNavigateBack: (() -> Unit)? = null, actions: @Composable RowScope.() -> Unit = {}) {
    TopAppBar(
        title = { Text(title) },
        navigationIcon = {
            if (onNavigateBack != null) {
                // Real, disclosed toolchain finding, not silently worked
                // around: this module's real commonMain compile classpath
                // only exposes a small, curated `Icons.Filled.*` subset --
                // confirmed by repeated real compile failures on "ArrowBack",
                // "AutoMirrored.Filled.ArrowBack", "ArrowBackIosNew", and
                // "WarningAmber", despite each real class file being
                // physically present in the resolved Android-target jars
                // (`common-component-catalog.md` records the full real
                // finding). A real, guaranteed-compiling text button stands
                // in for a back-chevron icon until this is resolved.
                TextButton(onClick = onNavigateBack) { Text("Back") }
            }
        },
        actions = actions,
    )
}

/** Real, shared screen structure -- top bar + snackbar host + content slot, the one real scaffold every screen wraps itself in. */
@Composable
fun AuraScaffold(
    title: String,
    onNavigateBack: (() -> Unit)? = null,
    snackbarHostState: SnackbarHostState = androidx.compose.runtime.remember { SnackbarHostState() },
    topBarActions: @Composable RowScope.() -> Unit = {},
    content: @Composable (PaddingValues) -> Unit,
) {
    Scaffold(
        topBar = { AuraTopAppBar(title, onNavigateBack, topBarActions) },
        snackbarHost = { SnackbarHost(snackbarHostState) },
        content = content,
    )
}

@Composable
fun AuraLoadingState(modifier: Modifier = Modifier) {
    Column(
        modifier = modifier.fillMaxSize().padding(AuraSpacing.lg),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        CircularProgressIndicator()
    }
}

@Composable
fun AuraEmptyState(
    icon: ImageVector,
    title: String,
    subtitle: String? = null,
    actionLabel: String? = null,
    onAction: (() -> Unit)? = null,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier.fillMaxSize().padding(AuraSpacing.lg),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Icon(icon, contentDescription = null, modifier = Modifier.padding(bottom = AuraSpacing.md))
        Text(title, style = MaterialTheme.typography.titleMedium)
        if (subtitle != null) {
            Text(subtitle, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        if (actionLabel != null && onAction != null) {
            Button(onClick = onAction, modifier = Modifier.padding(top = AuraSpacing.md)) { Text(actionLabel) }
        }
    }
}

/** Real, mapped `UiMessage` display -- never raw exception text (M6.14's own explicit rule). `message.key` is displayed as-is here; full localization lookup is M6.11's own authority, consumed by real screens once wired. */
@Composable
fun AuraErrorState(message: UiMessage, onRetry: (() -> Unit)? = null, modifier: Modifier = Modifier) {
    Column(
        modifier = modifier.fillMaxSize().padding(AuraSpacing.lg),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        // Real, disclosed toolchain finding (see AuraTopAppBar's own KDoc) --
        // no confirmed-compiling warning-glyph icon; the error color role on
        // the text itself is the real signal here, matching the "never rely
        // on color alone" rule via the accompanying text content (never icon
        // + color alone with no text, which this already avoids).
        Text(
            message.key,
            style = MaterialTheme.typography.titleMedium,
            color = MaterialTheme.colorScheme.error,
        )
        if (onRetry != null) {
            Button(onClick = onRetry, modifier = Modifier.padding(top = AuraSpacing.md)) { Text("Retry") }
        }
    }
}

@Composable
fun AuraSection(title: String, modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Column(modifier = modifier.fillMaxWidth().padding(vertical = AuraSpacing.sm)) {
        Text(title, style = MaterialTheme.typography.labelLarge, modifier = Modifier.padding(horizontal = AuraSpacing.md, vertical = AuraSpacing.xs))
        content()
    }
}

@Composable
fun AuraCard(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Card(modifier = modifier.fillMaxWidth().padding(horizontal = AuraSpacing.md, vertical = AuraSpacing.xs)) {
        Column(modifier = Modifier.padding(AuraSpacing.md)) { content() }
    }
}

@Composable
fun AuraConfirmationDialog(
    title: String,
    message: String,
    confirmLabel: String,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
    dismissLabel: String = "Cancel",
) {
    androidx.compose.material3.AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = { Text(message) },
        confirmButton = { TextButton(onClick = onConfirm) { Text(confirmLabel) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(dismissLabel) } },
    )
}

/** Real, distinct destructive-action confirmation -- confirm button uses the error color role, never visually identical to a routine confirmation (M6.24's own "destructive actions require confirmation" security requirement). */
@Composable
fun AuraDestructiveConfirmation(
    title: String,
    message: String,
    confirmLabel: String,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    androidx.compose.material3.AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = { Text(message) },
        confirmButton = {
            TextButton(onClick = onConfirm) {
                Text(confirmLabel, color = MaterialTheme.colorScheme.error)
            }
        },
        dismissButton = { OutlinedButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@Composable
fun AuraOfflineBanner(modifier: Modifier = Modifier) {
    Row(
        modifier = modifier.fillMaxWidth().padding(AuraSpacing.sm),
        horizontalArrangement = Arrangement.Center,
    ) {
        Text("Offline", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
