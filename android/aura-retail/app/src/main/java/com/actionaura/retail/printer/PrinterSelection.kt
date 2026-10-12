package com.actionaura.retail.printer

/** Which transport a receipt goes to. [key] is what is persisted -- never rename one. */
enum class PrinterType(val key: String) {
    NONE("none"), NETWORK("network"), BLUETOOTH("bluetooth"), USB("usb"), BUILTIN("builtin");

    companion object {
        fun fromKey(key: String?): PrinterType = values().firstOrNull { it.key == key } ?: NONE
    }
}

/**
 * Pure rules for "is this printer choice usable" and "may this print open the
 * drawer" (PRINT-2). No Android types, so the decisions that cost money when
 * wrong are unit-tested on the JVM ([PrinterSelectionTest]).
 */
object PrinterSelection {

    /** A transport is usable only when the thing it needs to find the printer is present. */
    fun isComplete(type: PrinterType, host: String, btMac: String, usbVid: Int, usbPid: Int): Boolean = when (type) {
        PrinterType.NONE -> false
        PrinterType.NETWORK -> host.isNotBlank()
        PrinterType.BLUETOOTH -> isMac(btMac)
        PrinterType.USB -> usbVid >= 0 && usbPid >= 0
        PrinterType.BUILTIN -> true     // the service itself is probed at print time
    }

    private val MAC = Regex("^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
    fun isMac(s: String): Boolean = MAC.matches(s)

    /**
     * Whether a built-in printer should be OFFERED at all: a Sunmi device whose
     * printer service resolves. Both, never either alone -- a non-Sunmi phone can
     * have the package by accident, and a Sunmi tablet without the service has
     * no printer to talk to.
     */
    fun builtInOffered(manufacturer: String?, serviceResolves: Boolean): Boolean =
        serviceResolves && manufacturer.equals("SUNMI", ignoreCase = true)

    /**
     * THE drawer rule on the phone. All three must hold:
     *  - the owner switched auto-kick on (default off),
     *  - this is the auto-print after a sale, not a manual reprint (a reprint
     *    must never pop the drawer a second time),
     *  - the SERVER says this sale may open it (cash, or a split with a cash
     *    part: `kick_allowed` from receipt-model). Absent/null means no.
     */
    fun kickDecision(autoKickPref: Boolean, isAutoPrint: Boolean, serverKickAllowed: Boolean?): Boolean =
        autoKickPref && isAutoPrint && serverKickAllowed == true
}
