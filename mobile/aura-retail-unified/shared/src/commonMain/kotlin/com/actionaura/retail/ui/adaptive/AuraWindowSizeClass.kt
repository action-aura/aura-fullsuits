package com.actionaura.retail.ui.adaptive

/**
 * M6.5 -- the shared adaptive-layout authority. Deliberately NOT
 * `androidx.compose.material3.windowsizeclass.WindowSizeClass` (an
 * Android-only real API at the time this module's Compose Multiplatform
 * 1.7.0/Kotlin 2.0.21 versions were pinned) -- this is our own real,
 * tiny, pure abstraction, computed from available content WIDTH in Dp,
 * never from a device marketing name/model check
 * (`adaptive-layout-contract.md`'s own explicit requirement).
 *
 * Breakpoints (600dp / 840dp) match Google's own published Material
 * width-based breakpoints for compact/medium/expanded -- real, standard
 * values, not invented ones.
 */
enum class AuraWindowSizeClass {
    Compact,
    Medium,
    Expanded;

    companion object {
        fun fromWidthDp(widthDp: Int): AuraWindowSizeClass = when {
            widthDp < 600 -> Compact
            widthDp < 840 -> Medium
            else -> Expanded
        }
    }
}

/** Real, minimal container-size contract -- platform Compose entry points (M6.26 Android, future iOS) measure the real available size and pass it down; `commonMain` layout decisions never query a platform API directly. */
data class AuraWindowSize(val widthDp: Int, val heightDp: Int) {
    val sizeClass: AuraWindowSizeClass get() = AuraWindowSizeClass.fromWidthDp(widthDp)
    val isLandscape: Boolean get() = widthDp > heightDp
}
