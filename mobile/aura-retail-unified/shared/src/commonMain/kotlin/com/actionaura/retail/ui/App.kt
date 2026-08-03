package com.actionaura.retail.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier

/**
 * Milestone 2 skeleton entry point -- proves the Compose Multiplatform
 * toolchain (commonMain UI, consumed identically by androidApp and, once a
 * Mac is available, iosApp) actually compiles and renders. Real screens
 * (Milestone 6) replace this; navigation graph and phase machine mirror
 * android/aura-retail's real AppRoot.kt (dashboard/pos/products/... routes,
 * SETUP/LOGIN/READY phases) once the shared repositories/use cases
 * (Milestone 5) exist to back them.
 */
@Composable
fun App() {
    MaterialTheme {
        Surface(modifier = Modifier.fillMaxSize()) {
            Column(
                modifier = Modifier.fillMaxSize(),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center,
            ) {
                Text("Aura Retail Unified -- Milestone 2 skeleton")
            }
        }
    }
}
