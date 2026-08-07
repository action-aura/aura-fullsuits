plugins {
    id("com.android.application")
    kotlin("android")
    kotlin("plugin.compose")
    id("org.jetbrains.compose")
}

android {
    namespace = "com.actionaura.retail.unified"
    // Real requirement found this milestone: app.cash.sqldelight:android-driver:2.3.2's
    // own AAR metadata requires consumers to compile against API 35+. This module
    // is independent of android/aura-retail's own compileSdk (separate Gradle
    // root, separate APK, ADR-1) so bumping it here has no cross-project effect.
    compileSdk = 35

    defaultConfig {
        // Distinct from com.actionaura.retail (the pre-unification app) for
        // the whole migration window -- both may be installed side by side
        // on the same device during Milestone 18's parity validation.
        // Cutover (switching this to com.actionaura.retail so it upgrades
        // the real installed app) is a deliberate, tested Milestone 18 step,
        // never a default.
        applicationId = "com.actionaura.retail.unified"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0-unified-dev"
    }

    compileOptions {
        // Matches android/aura-retail's own proven-working target (17) --
        // real error found in this module's first builds: 11 isn't
        // installed on this machine and no toolchain auto-download is
        // configured, so match the JDK Gradle itself already runs on
        // rather than requesting a JDK that would need to be provisioned.
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        compose = true
    }
}

kotlin {
    jvmToolchain(17)
}

dependencies {
    implementation(project(":shared"))
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.core:core-ktx:1.13.1")
    // Task 10 (multi-device-sync-foundation) -- MainActivity constructs the
    // real HttpClient(CIO) AuraAppContainer's SyncTransport needs (`shared`'s
    // own ktor-client-cio dependency is `implementation`-scoped, not `api`,
    // so it is not transitively visible here without this explicit
    // redeclaration -- matches this module's own established convention of
    // redeclaring rather than relying on implicit transitive exposure,
    // shared/build.gradle.kts's androidUnitTest ktor-client-cio comment).
    implementation("io.ktor:ktor-client-cio:2.3.12")
}
