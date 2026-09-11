package com.actionaura.retail.printer

import android.content.Context

/**
 * Per-device network-printer settings (receipt printing, retail-hardware-
 * viewports). Same SharedPreferences file ("aura_prefs") as AppLocale/
 * AppTheme in ui/i18n/Strings.kt:29-72, and the same load/set naming shape
 * -- but deliberately NOT held as observable Compose global state the way
 * those two are.
 *
 * AppLocale/AppTheme are read on every composition across the ENTIRE app
 * (every screen calls tr(), every screen is painted from the active
 * theme), so they are loaded once into memory at app startup (MainActivity)
 * and cached in Compose state so switching them recomposes instantly.
 * Printer settings are read in exactly two places -- the settings screen
 * itself, on entry, and the print call site, at print time -- and neither
 * needs live recomposition when the value changes elsewhere. Giving this a
 * cached-and-loaded-at-boot shape like AppTheme would require wiring a
 * `PrinterPrefs.load(ctx)` call into MainActivity's startup sequence
 * (outside this task's file scope) for correctness, and getting that wiring
 * wrong would silently read stale/default values at print time -- a strictly
 * worse failure mode than a plain SharedPreferences read on every call,
 * which is already cheap and always current. So: no cached global state,
 * no boot-time wiring required, no room for a stale read.
 *
 * Default OFF/empty, per this codebase's rule that a new feature must cost
 * an install that never uses it nothing: no host configured means no
 * printer UI is offered anywhere (see [isConfigured]), and auto-print
 * defaults to false.
 */
object PrinterPrefs {
    private const val PREFS = "aura_prefs"
    private const val KEY_HOST = "printer_host"
    private const val KEY_PORT = "printer_port"
    private const val KEY_WIDTH = "printer_width"
    private const val KEY_AUTO_PRINT = "printer_auto_print"

    const val DEFAULT_PORT = 9100
    const val WIDE_CHARS = 42   // 80mm paper
    const val NARROW_CHARS = 32 // 58mm paper

    private fun prefs(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun getHost(ctx: Context): String = prefs(ctx).getString(KEY_HOST, "") ?: ""
    fun getPort(ctx: Context): Int = prefs(ctx).getInt(KEY_PORT, DEFAULT_PORT)

    /** Always [WIDE_CHARS] or [NARROW_CHARS] -- never a stray persisted value,
     * even if a future bug (or a manual edit of the underlying prefs file)
     * wrote something else. */
    fun getWidth(ctx: Context): Int =
        if (prefs(ctx).getInt(KEY_WIDTH, WIDE_CHARS) == NARROW_CHARS) NARROW_CHARS else WIDE_CHARS

    fun getAutoPrint(ctx: Context): Boolean = prefs(ctx).getBoolean(KEY_AUTO_PRINT, false)

    fun set(ctx: Context, host: String, port: Int, width: Int, autoPrint: Boolean) {
        val safePort = if (port in 1..65535) port else DEFAULT_PORT
        val safeWidth = if (width == NARROW_CHARS) NARROW_CHARS else WIDE_CHARS
        prefs(ctx).edit()
            .putString(KEY_HOST, host.trim())
            .putInt(KEY_PORT, safePort)
            .putInt(KEY_WIDTH, safeWidth)
            .putBoolean(KEY_AUTO_PRINT, autoPrint)
            .apply()
    }

    /** True once a host has been entered. Gates whether "Print Receipt"
     * appears anywhere in the app -- an install that never opens this
     * settings section sees no new button and nothing behaves differently. */
    fun isConfigured(ctx: Context): Boolean = getHost(ctx).isNotBlank()
}
