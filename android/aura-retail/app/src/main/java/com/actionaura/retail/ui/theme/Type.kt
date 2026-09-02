package com.actionaura.retail.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

// The desktop token layer's type scale (11/12/14/15/17/22/30/40), mapped onto
// the M3 roles this app actually uses. Hierarchy comes from SCALE, not from
// drawing another box around things — same rule as the desktop.
//
// System sans rather than the desktop's bundled Plus Jakarta Sans: the woff2
// files the web ships cannot be consumed by Compose (it needs ttf/otf), and
// shipping a second copy of a webfont inside the APK is a decision for
// whoever owns APK size — noted as a known gap, not smuggled in.
private val Sans = FontFamily.SansSerif

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
