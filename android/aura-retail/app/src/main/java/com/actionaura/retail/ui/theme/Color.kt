package com.actionaura.retail.ui.theme

import androidx.compose.ui.graphics.Color

// ─────────────────────────────────────────────────────────────────────────────
// OPERATIONAL CALM, dark — the phone edition of the desktop till's token layer.
//
// The desktop (products/retail/frontend/css/main.css, [design-tokens]) went
// through a deliberate, contrast-TESTED redesign: one accent used semantically,
// calm cool-grey surfaces, colour that carries meaning (money in, money out,
// warning, refusal) — and it explicitly rejected the "near-black HUD + neon
// accents" look as fatiguing and untrustworthy for software that moves money.
// This file used to be exactly that rejected look (an "Aurora" nebula: teal
// glow constants that had silently become rose, tinted shadows, gradient
// text). Worse, its names had drifted from their values: AuroraTeal was rose,
// AuroraCyan was rose-400, AuroraViolet was rose-300 — a theme file that lied
// to whoever read it.
//
// These values are the desktop's OWN dark-mode palette (the dark token set
// solved by the same WCAG math as retail_design_contrast_test.js: every text
// token AA 4.5:1 against every surface it can land on, accent label 4.5:1 on
// the accent fill). Owner's requirement, verbatim: "the desktop retail and the
// phone should look close like basically they supposed to be known for
// eachother". Same surfaces, same accent, same state colours = same product.
//
// NAMING RULE (inherited from the desktop token layer): a token is named for
// WHAT IT IS FOR, never for what it looks like. If the palette is ever
// re-themed, these names stay true.
// ─────────────────────────────────────────────────────────────────────────────

// ── SURFACES — ordered by elevation, named by job ────────────────────────────
val SurfaceApp = Color(0xFF0F1319)     // outermost shell; the window background
val SurfaceSunken = Color(0xFF0B0F15)  // wells, inputs, anything you type into
val SurfacePanel = Color(0xFF151A23)   // chrome: top bar, tab bar
val SurfaceRaised = Color(0xFF171D27)  // cards lifted off the shell
val SurfaceTill = Color(0xFF1A212C)    // THE working surface: cart, active list
val SurfaceHover = Color(0xFF212936)   // finger is over it
val SurfaceActive = Color(0xFF273140)  // pressed / selected row

// ── TEXT — three weights of emphasis, all AA+ on every surface above ─────────
val TextPrimary = Color(0xFFEDF2F8)    // headings, values, anything load-bearing
val TextSecondary = Color(0xFFC3CDDB)  // body copy, labels
val TextTertiary = Color(0xFF9FADC0)   // meta: timestamps, hints, captions

// ── ACCENT — ONE accent, and it means "this is the action you take" ──────────
// Same hue as the desktop's --accent-action, lightened for dark surfaces the
// way the desktop's own dark set does. Deliberately NOT rose: the previous
// rose accent sat in the refusal/danger hue, so "act here" and "something is
// wrong" were the same colour at a glance — on a till, the one ambiguity that
// costs real money.
val AccentAction = Color(0xFF6EA8FF)
val OnAccent = Color(0xFF0D1B2E)       // label on an accent fill (4.5:1+)
val AccentSoft = Color(0xFF1C2A44)     // tinted BACKDROP (selected tab), not a fill

// ── BORDERS — named by weight of separation ──────────────────────────────────
val BorderDefault = Color(0xFF2E3947)  // cards, inputs, the normal case
val BorderHairline = Color(0xFF232B37) // row rules, dividers

// ── SEMANTIC STATE — one meaning per colour (desktop dark set) ───────────────
// *Text* variants: AA on every surface. The *Container* variants are their
// quiet backdrops (badge fills), each pairing AA with its own text colour.
val Success = Color(0xFF7BD9A2)
val Warning = Color(0xFFE6C67A)
val Danger = Color(0xFFFF9D94)
val Info = Color(0xFF8AB5F8)
val SuccessContainer = Color(0xFF12301F)
val DangerContainer = Color(0xFF3D1713)
