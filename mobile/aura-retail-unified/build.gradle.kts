plugins {
    // Version pinned to match android/aura-retail's existing Kotlin (2.0.21)
    // so this module and the pre-unification app can coexist in CI without
    // a compiler-version mismatch during the Milestone 18 migration window.
    kotlin("multiplatform") version "2.0.21" apply false
    kotlin("plugin.compose") version "2.0.21" apply false
    id("com.android.application") version "8.5.2" apply false
    id("com.android.library") version "8.5.2" apply false
    id("org.jetbrains.compose") version "1.7.0" apply false
}

tasks.register("clean", Delete::class) {
    delete(rootProject.layout.buildDirectory)
}
