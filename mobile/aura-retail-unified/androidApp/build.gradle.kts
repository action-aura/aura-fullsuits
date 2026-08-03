plugins {
    id("com.android.application")
    kotlin("android")
    kotlin("plugin.compose")
    id("org.jetbrains.compose")
}

android {
    namespace = "com.actionaura.retail.unified"
    compileSdk = 34

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
}
