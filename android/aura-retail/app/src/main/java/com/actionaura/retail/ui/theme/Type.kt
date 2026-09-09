package com.actionaura.retail.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import com.actionaura.retail.R

// The desktop token layer's type scale (11/12/14/15/17/22/30/40), mapped onto
// the M3 roles this app actually uses. Hierarchy comes from SCALE, not from
// drawing another box around things — same rule as the desktop.
//
// This now matches the desktop's typeface instead of the platform sans. The
// earlier note here said the web's woff2 files "cannot be consumed by Compose
// (it needs ttf/otf)" — true of woff2 itself, but the compression is a
// container, and decompressing to TTF is lossless. What actually made this
// non-trivial is that the two faces are DISJOINT: measured, Plus Jakarta Sans
// carries 0 Arabic glyphs and IBM Plex Sans Arabic carries 0 Latin ones. The
// desktop resolves that per character in a CSS font stack. A Compose
// FontFamily cannot: it picks a face by weight and style, never by script,
// and an app-supplied typeface gets no system fallback chain. Bundling either
// face alone would have rendered every character of the other script as tofu
// on a Jordanian till — strictly worse than the platform sans it replaced.
//
// So res/font holds one MERGED face per weight (Latin from Jakarta, Arabic
// from Plex), built and verified before being committed: each shipped file
// covers 95/95 Latin, 252/256 Arabic and all 10 Arabic-Indic digits, and
// HarfBuzz shaping on them substitutes real initial/medial/final forms rather
// than isolated letters, with zero .notdef in either script. Both are OFL;
// derivation, weight mapping and licences are in res/raw/aura_sans_notice.txt.
//
// Costs 490 KB uncompressed in res/font. Only the four weights the styles
// below actually request are bundled — Jakarta's 800 is not, because nothing
// here asks for it.
private val Sans = FontFamily(
    Font(R.font.aura_sans_regular, FontWeight.Normal),
    Font(R.font.aura_sans_medium, FontWeight.Medium),
    Font(R.font.aura_sans_semibold, FontWeight.SemiBold),
    Font(R.font.aura_sans_bold, FontWeight.Bold),
)

val AuraTypography = Typography(
    // --text-size-total (40): the number a customer reads from a metre away.
    displaySmall = TextStyle(fontFamily = Sans, fontWeight = FontWeight.Bold, fontSize = 40.sp, lineHeight = 48.sp),
    // --text-size-display (30): screen heroes, KPI values.
    headlineMedium = TextStyle(fontFamily = Sans, fontWeight = FontWeight.Bold, fontSize = 30.sp, lineHeight = 36.sp),
    // --text-size-title (22).
    headlineSmall = TextStyle(fontFamily = Sans, fontWeight = FontWeight.Bold, fontSize = 22.sp, lineHeight = 28.sp),
    titleLarge = TextStyle(fontFamily = Sans, fontWeight = FontWeight.Bold, fontSize = 22.sp, lineHeight = 28.sp),
    // --text-size-subhead (17).
    titleMedium = TextStyle(fontFamily = Sans, fontWeight = FontWeight.SemiBold, fontSize = 17.sp, lineHeight = 24.sp),
    // --text-size-body-lg (15) / --text-size-body (14).
    bodyLarge = TextStyle(fontFamily = Sans, fontWeight = FontWeight.Normal, fontSize = 15.sp, lineHeight = 22.sp),
    bodyMedium = TextStyle(fontFamily = Sans, fontWeight = FontWeight.Normal, fontSize = 14.sp, lineHeight = 20.sp),
    // --text-size-meta (12).
    bodySmall = TextStyle(fontFamily = Sans, fontWeight = FontWeight.Normal, fontSize = 12.sp, lineHeight = 16.sp),
    labelLarge = TextStyle(fontFamily = Sans, fontWeight = FontWeight.SemiBold, fontSize = 14.sp, lineHeight = 20.sp),
    labelMedium = TextStyle(fontFamily = Sans, fontWeight = FontWeight.Medium, fontSize = 12.sp, lineHeight = 16.sp),
    // --text-size-micro (11): tab-bar captions, never smaller.
    labelSmall = TextStyle(fontFamily = Sans, fontWeight = FontWeight.Medium, fontSize = 11.sp, lineHeight = 16.sp),
)
