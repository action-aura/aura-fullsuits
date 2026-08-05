import org.jetbrains.kotlin.gradle.ExperimentalKotlinGradlePluginApi
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    kotlin("multiplatform")
    kotlin("plugin.compose")
    id("com.android.library")
    id("org.jetbrains.compose")
    id("app.cash.sqldelight")
}

kotlin {
    @OptIn(ExperimentalKotlinGradlePluginApi::class)
    androidTarget {
        compilerOptions {
            jvmTarget.set(JvmTarget.JVM_17) // matches android/aura-retail's own proven target; JDK 11 isn't installed on this host
        }
    }

    // Apple targets. Kotlin/Native's Apple-target compiler/linker requires a
    // macOS host with Xcode -- see docs/retail/unified_mobile/
    // ios-build-readiness-plan.md for the exact real result of attempting
    // this on the actual (Windows) development host used for this
    // initiative. Declared unconditionally so the source-set structure,
    // expect/actual contracts, and iosMain adapters are always real,
    // buildable-on-a-Mac code -- never removed to make a Windows sync
    // "succeed" by hiding the target.
    iosX64()
    iosArm64()
    iosSimulatorArm64()

    sourceSets {
        val commonMain by getting {
            dependencies {
                implementation(compose.runtime)
                implementation(compose.foundation)
                implementation(compose.material3)
                implementation(compose.components.resources)
                implementation(compose.ui)
                implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.9.0")
                implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.3")
                implementation("org.jetbrains.kotlinx:kotlinx-datetime:0.6.1")
                // Real, evidence-based decision -- see docs/retail/unified_mobile/
                // money-decimal-decision.md. Version pinned (not a floating range)
                // so an upstream release can never silently change financial
                // behavior; wrapped entirely behind financial/Money.kt etc. so this
                // is the only place BigDecimal is referenced directly.
                implementation("com.ionspin.kotlin:bignum:0.3.10")
                // M4 -- real, evidence-based choice, see docs/retail/unified_mobile/
                // shared-database-schema-decision.md. Schema lives in
                // shared/src/commonMain/sqldelight/.
                implementation("app.cash.sqldelight:runtime:2.3.2")
                implementation("app.cash.sqldelight:coroutines-extensions:2.3.2")
                // M6.2/M6.7 -- real, official Compose Multiplatform authorities
                // for shared ViewModel lifecycle and type-safe navigation
                // (JetBrains' own KMP forks of androidx.lifecycle/androidx.navigation),
                // not a hand-rolled substitute -- see
                // docs/retail/unified_mobile/shared-viewmodel-lifecycle.md and
                // type-safe-navigation-contract.md for the real, evidence-based
                // version-compatibility check against this module's pinned
                // Kotlin 2.0.21 / Compose Multiplatform 1.7.0.
                implementation("org.jetbrains.androidx.lifecycle:lifecycle-viewmodel-compose:2.8.2")
                implementation("org.jetbrains.androidx.navigation:navigation-compose:2.8.0-alpha10")
            }
        }
        val commonTest by getting {
            dependencies {
                implementation(kotlin("test"))
                implementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.9.0")
            }
        }
        val androidMain by getting {
            dependencies {
                implementation("androidx.activity:activity-compose:1.9.2")
                implementation("androidx.security:security-crypto:1.1.0-alpha06")
                implementation("app.cash.sqldelight:android-driver:2.3.2")
            }
        }
        val androidUnitTest by getting {
            dependencies {
                implementation(kotlin("test"))
                // JVM/JDBC SQLite driver -- for real schema verification in
                // plain unit tests, which cannot use AndroidSqliteDriver
                // (that needs a real Android Context/instrumentation).
                implementation("app.cash.sqldelight:sqlite-driver:2.3.2")
            }
        }
        // Kotlin's Default Hierarchy Template (on by default since Kotlin
        // 1.9.20) creates and wires iosMain/iosTest as the shared parent of
        // iosX64/iosArm64/iosSimulatorArm64 whenever those targets are
        // actually configured. Real finding from this module's first
        // Gradle syncs on this (Windows) host: with
        // kotlin.native.ignoreDisabledTargets=true, Kotlin/Native silently
        // drops all three Apple targets entirely on a non-Mac host -- so
        // `iosMain` does not exist to reference here at all when synced on
        // Windows. No dependencies are added to iosMain/iosTest yet (no
        // Milestone-19 iOS-specific code exists), so nothing here needs to
        // reference them -- the source directories remain in place on disk
        // for that milestone's real Kotlin/Native code, written and
        // maintained even though this host cannot compile them. See
        // docs/retail/unified_mobile/ios-build-readiness-plan.md for the
        // exact real Gradle error this produced and what it means.
        //
        // M4 addition: when this project is next synced on a Mac and iosMain
        // really exists, add `implementation("app.cash.sqldelight:native-driver:2.3.2")`
        // to it (the real iOS SQLite driver) -- not addable here for the same
        // reason nothing else is.
    }
}

sqldelight {
    databases {
        create("RetailDatabase") {
            packageName.set("com.actionaura.retail.db")
        }
    }
}

android {
    namespace = "com.actionaura.retail.shared"
    compileSdk = 35 // see androidApp/build.gradle.kts's comment -- sqldelight:android-driver:2.3.2 requires it
    defaultConfig {
        minSdk = 26
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
