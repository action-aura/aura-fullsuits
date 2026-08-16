package com.actionaura.retail.ui.theme

import androidx.compose.ui.graphics.Color

// Brand accent -- was teal/emerald, unified with desktop's rose #f43f5e
// (see the Aurora constants below for the full rationale). Teal500 is only
// referenced by the dead LightColors scheme (Theme.kt) and left as-is.
val Teal500 = Color(0xFF14B8A6)
val Teal600 = Color(0xFFE11D48)  // rose-600 -- primaryContainer
val Teal200 = Color(0xFFFECDD3)  // rose-200 -- onPrimaryContainer

// Light scheme
val LightBackground = Color(0xFFF5F7FA)
val LightSurface = Color(0xFFFFFFFF)
val LightSurfaceVariant = Color(0xFFEEF1F6)
val LightOnSurface = Color(0xFF1A2230)
val LightOnSurfaceVariant = Color(0xFF51607A)
val LightOutline = Color(0xFFD3DAE3)

// Dark scheme
val DarkBackground = Color(0xFF0B0F14)
val DarkSurface = Color(0xFF131A22)
val DarkSurfaceVariant = Color(0xFF1C2530)
val DarkOnSurface = Color(0xFFE6EAF0)
val DarkOnSurfaceVariant = Color(0xFF9AA7B8)
val DarkOutline = Color(0xFF2C3744)

// Status colors (shared)
val Success = Color(0xFF10B981)
val Warning = Color(0xFFF59E0B)
val Danger = Color(0xFFEF4444)
val Info = Color(0xFF38BDF8)

// ── Aurora (next-gen dark identity) ───────────────────────────────────────────
val Ink = Color(0xFF070B14)        // background base (deep blue-black)
val Ink2 = Color(0xFF0C1220)       // background gradient end
val AuroraSurface = Color(0xFF121A2B)    // frosted panel
val AuroraSurfaceHi = Color(0xFF1A2436)  // elevated panel
val AuroraOnSurface = Color(0xFFEAF0FF)
val AuroraMuted = Color(0xFF9AA7C2)
val AuroraOutline = Color(0xFF243149)

// Brand accent unified with the desktop web app's own single accent color
// (products/retail/frontend/app-shell.js's per-subsystem --sub-accent for
// Retail is #f43f5e, injected at runtime into main.css's token system) --
// these three constants were teal/cyan/violet before, an entirely different
// brand color from desktop with no shared reference point. Now a
// monochromatic rose ramp (rose-500/400/300) so the aurora gradient reads
// as one consistent brand, not three unrelated hues.
val AuroraTeal = Color(0xFFF43F5E)   // primary glow -- same hex as desktop's --sub-accent
val AuroraCyan = Color(0xFFFB7185)   // secondary glow (rose-400)
val AuroraViolet = Color(0xFFFDA4AF) // accent glow (rose-300)
