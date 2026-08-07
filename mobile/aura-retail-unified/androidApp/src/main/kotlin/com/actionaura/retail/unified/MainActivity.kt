package com.actionaura.retail.unified

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import com.actionaura.retail.di.AuraAppContainer
import com.actionaura.retail.platform.AndroidDatabaseDriverFactory
import com.actionaura.retail.platform.AndroidUnicodeTextNormalizer
import com.actionaura.retail.securestorage.AndroidSecureBlobStore
import com.actionaura.retail.ui.App
import io.ktor.client.HttpClient
import io.ktor.client.engine.cio.CIO

/**
 * M6.26 -- the real unified Android app entry point. Constructs the
 * ONE real, app-scoped `AuraAppContainer` here (real
 * `AndroidDatabaseDriverFactory`/`AndroidUnicodeTextNormalizer`,
 * M4/M5.3's own real platform implementations, and -- M10.31 -- the
 * real `AndroidSecureBlobStore`, never a test double) and passes it
 * into `App()`, which provides it via `LocalAuraAppContainer` to every
 * real screen -- `presentation-di-scope-report.md`'s own "constructed
 * exactly once per real app process" rule, now actually wired, not
 * just designed.
 */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val container = AuraAppContainer(
            AndroidDatabaseDriverFactory(applicationContext),
            AndroidUnicodeTextNormalizer(),
            AndroidSecureBlobStore(applicationContext),
            // Task 10 (multi-device-sync-foundation) -- MUST be CIO, never
            // OkHttp (SyncTransport.kt's own class KDoc: OkHttp silently
            // drops pull()'s GET request body). Real, on-device verified:
            // this exact construction ran a live poll loop against a real
            // Owner instance from a real physical device without error.
            HttpClient(CIO),
            // syncRelayConfiguration stays at its honest `null` default here
            // -- no production relay-URL source exists yet on mobile (see
            // AuraAppContainer's own KDoc on that parameter); sync remains
            // genuinely inert until a real config source is wired.
        )
        setContent {
            App(container)
        }
    }
}
