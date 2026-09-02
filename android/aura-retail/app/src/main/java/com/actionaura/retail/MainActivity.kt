package com.actionaura.retail

import android.os.Bundle
import android.view.KeyEvent
import android.view.inputmethod.InputMethodManager
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.actionaura.retail.barcode.HidScanBus
import com.actionaura.retail.ui.AppRoot
import com.actionaura.retail.ui.i18n.AppLocale
import com.actionaura.retail.ui.theme.AuraTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        // Restore the user's chosen language before composing any UI.
        AppLocale.load(this)
        super.onCreate(savedInstanceState)
        // The app's identity is fixed-dark (see ui/theme/Theme.kt), so the
        // system bars must always use LIGHT icons. The no-arg overload picks
        // icon colour from the SYSTEM theme -- in system light mode that put
        // dark icons over this app's dark background, an unreadable clock on
        // every screen.
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
        )
        setContent {
            AuraTheme {
                AppRoot()
            }
        }
    }

    // Wave 1B (Part L/M): USB-OTG / Bluetooth HID ("keyboard wedge") barcode
    // scanner support, mirroring products/retail/frontend/subsystem-retail.js's
    // proven Windows engine -- see HidScanDetector.kt's docstring for the full
    // rationale. Activity-level dispatchKeyEvent (not a Compose key-event
    // modifier) is used deliberately: it observes every hardware key event
    // regardless of which composable currently holds focus, matching the
    // Windows engine's document-level capture-phase listener, and avoids
    // fighting Compose's own focus-routing system for something that must
    // work everywhere a scan could occur.
    //
    // Never intercepts characters while a real text field has focus with an
    // open soft keyboard (isAcceptingText) -- normal typing into any field
    // must never lose a character, mirroring the Windows engine's identical
    // "never disturb an editable field" rule.
    //
    // @Suppress("RestrictedApi"): known androidx lint false positive -- this
    // overrides the public android.app.Activity.dispatchKeyEvent method (via
    // androidx.activity.ComponentActivity), not a restricted androidx.core
    // internal API; lint's metadata conflates the two same-named methods.
    @Suppress("RestrictedApi")
    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        if (event.action == KeyEvent.ACTION_DOWN) {
            val imm = getSystemService(INPUT_METHOD_SERVICE) as? InputMethodManager
            val typingIntoField = imm?.isAcceptingText == true
            if (!typingIntoField) {
                val now = event.eventTime
                when {
                    event.keyCode == KeyEvent.KEYCODE_ENTER || event.keyCode == KeyEvent.KEYCODE_NUMPAD_ENTER ->
                        HidScanBus.onEnter(now)
                    else -> {
                        val ch = event.unicodeChar
                        if (ch != 0) HidScanBus.onChar(ch.toChar(), now)
                    }
                }
            }
        }
        return super.dispatchKeyEvent(event)
    }
}
