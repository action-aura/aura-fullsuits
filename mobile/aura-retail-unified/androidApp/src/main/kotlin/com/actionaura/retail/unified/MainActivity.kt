package com.actionaura.retail.unified

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import com.actionaura.retail.di.AuraAppContainer
import com.actionaura.retail.platform.AndroidDatabaseDriverFactory
import com.actionaura.retail.platform.AndroidUnicodeTextNormalizer
import com.actionaura.retail.ui.App

/**
 * M6.26 -- the real unified Android app entry point. Constructs the
 * ONE real, app-scoped `AuraAppContainer` here (real
 * `AndroidDatabaseDriverFactory`/`AndroidUnicodeTextNormalizer`,
 * M4/M5.3's own real platform implementations) and passes it into
 * `App()`, which provides it via `LocalAuraAppContainer` to every real
 * screen -- `presentation-di-scope-report.md`'s own "constructed
 * exactly once per real app process" rule, now actually wired, not
 * just designed.
 */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val container = AuraAppContainer(AndroidDatabaseDriverFactory(applicationContext), AndroidUnicodeTextNormalizer())
        setContent {
            App(container)
        }
    }
}
