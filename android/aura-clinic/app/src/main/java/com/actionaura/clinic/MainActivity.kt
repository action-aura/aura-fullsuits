package com.actionaura.clinic

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.actionaura.clinic.ui.AppRoot
import com.actionaura.clinic.ui.i18n.AppLocale
import com.actionaura.clinic.ui.theme.AuraTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        // Restore the user's chosen language before composing any UI.
        AppLocale.load(this)
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            AuraTheme {
                AppRoot()
            }
        }
    }
}
