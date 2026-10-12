package com.actionaura.retail.ui.screens

import androidx.compose.material3.AlertDialog
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.unit.LayoutDirection
import com.actionaura.retail.ui.i18n.AppLocale

/**
 * An AlertDialog whose CONTENT follows the app's language direction.
 *
 * Found on the emulator, in Arabic: the app root forces RTL through
 * LocalLayoutDirection, but a dialog is its own window and lays out in the
 * SYSTEM direction, so the new Cash Movement dialog rendered its title, chips
 * and fields flush LEFT in an otherwise right-to-left screen. Re-providing the
 * app's direction inside each slot fixes the content (alignment, chip order,
 * field text). The dialog's own button row is placed by the window and stays in
 * the system direction -- a known cosmetic limit, recorded in ROADMAP.
 */
@Composable
internal fun LocalizedAlertDialog(
    onDismissRequest: () -> Unit,
    title: @Composable () -> Unit,
    text: @Composable () -> Unit,
    confirmButton: @Composable () -> Unit,
    dismissButton: @Composable (() -> Unit)? = null,
) {
    val dir = if (AppLocale.isRtl) LayoutDirection.Rtl else LayoutDirection.Ltr
    AlertDialog(
        onDismissRequest = onDismissRequest,
        title = { CompositionLocalProvider(LocalLayoutDirection provides dir, content = title) },
        text = { CompositionLocalProvider(LocalLayoutDirection provides dir, content = text) },
        confirmButton = { CompositionLocalProvider(LocalLayoutDirection provides dir, content = confirmButton) },
        dismissButton = dismissButton?.let { d -> { CompositionLocalProvider(LocalLayoutDirection provides dir, content = d) } },
    )
}
