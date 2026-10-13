package com.actionaura.retail.ui.pos

import com.actionaura.retail.ui.i18n.PIN_LENGTH

/**
 * A keypad edit as a pure function -- the phone's twin of the desktop's `_padEdit` (the discount
 * sheet and the cash pad share it there): it appends or edits a decimal string the way a calculator
 * does. No leading zeros, one ".", at most [dp] decimals (a further digit is ignored, never rounded),
 * "00" does nothing on an empty box, "back" drops the last character, at most 9 digits in all.
 */
fun padEdit(value: String, key: String, dp: Int): String {
    if (key == KEY_BACK) return value.dropLast(1)
    if (key == ".") {
        if (dp == 0 || '.' in value) return value
        return (if (value.isEmpty()) "0" else value) + "."
    }
    if (key == "00" && (value.isEmpty() || value == "0")) return value
    val base = if (value == "0") "" else value
    val next = base + key
    val dot = next.indexOf('.')
    if (dot != -1 && next.length - dot - 1 > dp) return value
    if (next.replace(".", "").length > 9) return value
    return next
}

/** A manager PIN edit: digits only, at most [PIN_LENGTH], "back" drops the last. */
fun pinEdit(value: String, key: String): String = when {
    key == KEY_BACK -> value.dropLast(1)
    key.length == 1 && key[0] in '0'..'9' && value.length < PIN_LENGTH -> value + key
    else -> value
}

const val KEY_BACK = "back"
