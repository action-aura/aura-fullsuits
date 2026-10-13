package com.actionaura.retail.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.material3.AlertDialogDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.ProvideTextStyle
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.actionaura.retail.ui.i18n.AppLocale

/**
 * An alert dialog that follows the app's language direction END TO END.
 *
 * Found on the emulator, in Arabic, twice. The app root forces RTL through
 * LocalLayoutDirection, but a dialog is its own window and lays out in the
 * SYSTEM direction. ANDROID-2a re-provided the direction inside each slot of
 * Material's AlertDialog, which fixed the content (chips, fields) but not the
 * two things the dialog places ITSELF: the title sat flush LEFT and the button
 * row kept its left-to-right order. Those are positioned by AlertDialog's own
 * layout, outside every slot, so no slot can reach them.
 *
 * So this no longer wraps AlertDialog. It is a [Dialog] whose whole body --
 * surface, title, text and button row -- is composed INSIDE the direction
 * provider, so the title aligns to the start edge of the active language and
 * the buttons mirror (confirm at the end edge: left in Arabic). Same
 * dimensions and colours as Material's AlertDialog (taken from
 * [AlertDialogDefaults]), so an English dialog looks as it always did.
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
    Dialog(
        onDismissRequest = onDismissRequest,
        properties = DialogProperties(usePlatformDefaultWidth = false),
    ) {
        CompositionLocalProvider(LocalLayoutDirection provides dir) {
            Surface(
                modifier = Modifier.padding(horizontal = 24.dp).widthIn(min = 280.dp, max = 560.dp),
                shape = AlertDialogDefaults.shape,
                color = AlertDialogDefaults.containerColor,
                tonalElevation = AlertDialogDefaults.TonalElevation,
            ) {
                Column(Modifier.padding(24.dp)) {
                    CompositionLocalProvider(LocalContentColor provides AlertDialogDefaults.titleContentColor) {
                        ProvideTextStyle(MaterialTheme.typography.headlineSmall) {
                            Box(Modifier.fillMaxWidth().padding(bottom = 16.dp), contentAlignment = Alignment.CenterStart) { title() }
                        }
                    }
                    CompositionLocalProvider(LocalContentColor provides AlertDialogDefaults.textContentColor) {
                        ProvideTextStyle(MaterialTheme.typography.bodyMedium) {
                            Box(Modifier.fillMaxWidth()) { text() }
                        }
                    }
                    Row(
                        Modifier.fillMaxWidth().padding(top = 24.dp),
                        horizontalArrangement = Arrangement.End,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        dismissButton?.let { it(); Spacer(Modifier.width(8.dp)) }
                        confirmButton()
                    }
                }
            }
        }
    }
}

