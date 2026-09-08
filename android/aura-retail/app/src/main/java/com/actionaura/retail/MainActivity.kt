package com.actionaura.retail

import android.graphics.drawable.ColorDrawable
import android.os.Bundle
import android.view.KeyEvent
import android.view.inputmethod.InputMethodManager
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.ui.graphics.toArgb
import com.actionaura.retail.barcode.HidScanBus
import com.actionaura.retail.ui.AppRoot
import com.actionaura.retail.ui.i18n.AppLocale
import com.actionaura.retail.ui.i18n.AppTheme
import com.actionaura.retail.ui.theme.AuraPalette
import com.actionaura.retail.ui.theme.AuraTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        // Restore the user's chosen language and theme before composing any UI.
        AppLocale.load(this)
        AppTheme.load(this)
        super.onCreate(savedInstanceState)

        // SYSTEM-BAR ICON POLARITY follows the RESTORED PALETTE, not a fixed
        // identity. This used to pin both bars to SystemBarStyle.dark(...) on
        // the premise that "the app's identity is fixed-dark"; that premise
        // died with the five-theme switch (Theme.kt now has a lightColorScheme
        // branch and AuraPalette.ALL ships two light palettes, Day and Sand).
        //
        // SystemBarStyle.dark forces detectDarkMode=true, i.e.
        // setAppearanceLight{Status,Navigation}Bars(false) -- WHITE icons
        // unconditionally. The app is edge-to-edge with transparent bars and
        // AppBackground paints colorScheme.background (= surfaceApp) behind
        // them, so on Day (#EAEEF3) that is white-on-near-white at 1.17:1, and
        // on Sand (#EFE8DC) 1.22:1: the clock, the battery and the gesture pill
        // effectively vanish. The correct dark glyph (#141A24) measures
        // 14.98:1 and 14.34:1 on those two grounds.
        //
        // The no-arg overload is still wrong for the opposite reason it was
        // wrong before -- it reads the SYSTEM theme, which says nothing about
        // which of the five Aura palettes this till is running.
        val startupIsDark = AuraPalette.current.isDark
        val barStyle =
            if (startupIsDark) SystemBarStyle.dark(android.graphics.Color.TRANSPARENT)
            else SystemBarStyle.light(android.graphics.Color.TRANSPARENT,
                                      android.graphics.Color.TRANSPARENT)
        enableEdgeToEdge(statusBarStyle = barStyle, navigationBarStyle = barStyle)

        // This only fixes the polarity the activity STARTS with. The theme
        // switches at runtime with no Activity restart (AppTheme.set writes
        // straight into AuraPalette.current's Compose state), so AuraTheme
        // re-applies the appearance flags on every change -- see Theme.kt.

        // Pre-Compose window ground. @color/window_background (themes.xml) is a
        // single fixed value and there are five palettes, so it can only ever
        // match one of them; repainting here, right after AppTheme.load()
        // restored the choice, makes the window agree with the palette from
        // attach until the first Compose frame.
        //
        // HONEST LIMIT: this CANNOT change the system's starting/preview
        // window, which the framework draws from the manifest theme's
        // windowBackground before this process draws anything. Killing that
        // last frame needs per-palette launch themes (five <style>s plus an
        // Activity.setTheme before super.onCreate), which is a bigger change
        // than this defect warrants.
        window.setBackgroundDrawable(ColorDrawable(AuraPalette.current.surfaceApp.toArgb()))
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
