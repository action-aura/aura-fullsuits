package com.actionaura.clinic.ui.components

import android.app.Activity
import android.view.WindowManager
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.ui.platform.LocalView

/**
 * Phase 4M / Wave 1A screen-protection decision (Option B: screen-level,
 * not application-wide) -- see
 * docs/mobile/wave1a/clinic-screen-protection-decision.md for the full
 * rationale and the on-device verification of what this actually does.
 *
 * `FLAG_SECURE` is a *window*-level flag (this app is single-Activity,
 * Navigation-Compose-based -- there is no per-screen Window to attach it
 * to individually). This composable approximates "screen-level" by
 * setting the flag on the shared Activity window only while a sensitive
 * screen is present in composition, and clearing it when that screen
 * leaves composition -- so non-sensitive screens (dashboard, patient
 * list, appointments, settings) are unaffected, but a patient's medical
 * detail/billing screen is not captured by the recent-apps thumbnail or
 * screenshots while it's the one visible on screen.
 *
 * Known limitation (documented, not hidden): toggling FLAG_SECURE assumes
 * only one sensitive screen is on the back stack at a time, which matches
 * this app's actual navigation graph (patient-detail and billing are not
 * nested inside each other) -- if that ever changes, this needs revisiting
 * rather than assuming it still holds.
 */
@Composable
fun SecureScreen() {
    val view = LocalView.current
    DisposableEffect(Unit) {
        val activity = view.context as? Activity
        activity?.window?.setFlags(
            WindowManager.LayoutParams.FLAG_SECURE,
            WindowManager.LayoutParams.FLAG_SECURE,
        )
        onDispose {
            activity?.window?.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
        }
    }
}
