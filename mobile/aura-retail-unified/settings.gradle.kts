// Aura Retail Unified Mobile (Android + iOS) -- one shared Kotlin
// Multiplatform / Compose Multiplatform product. Independent Gradle root
// from android/aura-retail (the pre-unification Chaquopy-based app, which
// remains the migration baseline per Milestone 18 until cutover proves
// safe -- see docs/retail/unified_mobile/).
//
// iosApp is NOT a Gradle module -- it is a real Xcode project (Milestone 19)
// that consumes the `shared` module's compiled Kotlin/Native framework.
// This mirrors the standard KMP project layout, not a workaround.

pluginManagement {
    repositories {
        google()
        gradlePluginPortal()
        mavenCentral()
        maven("https://maven.pkg.jetbrains.space/public/p/compose/dev")
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
        maven("https://maven.pkg.jetbrains.space/public/p/compose/dev")
    }
}

rootProject.name = "aura-retail-unified"

include(":shared")
include(":androidApp")
