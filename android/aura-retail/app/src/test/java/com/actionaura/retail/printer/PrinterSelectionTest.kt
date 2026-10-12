package com.actionaura.retail.printer

import com.google.common.truth.Truth.assertThat
import org.junit.Test

/**
 * The decisions that cost money or hide a feature when wrong: which printer
 * choices are usable, when the Built-in option appears, and -- most important --
 * when a print may open the cash drawer. Both directions of the kick rule are
 * pinned: it must DENY (card sale, reprint, pref off, server silent) and it
 * must ALLOW (auto-print of a cash sale with the pref on), because a rule that
 * always says no would pass every deny test and silently kill the feature.
 */
class PrinterSelectionTest {

    // ── kick ──────────────────────────────────────────────────────────────
    @Test fun auto_print_of_a_cash_sale_with_the_pref_on_kicks() {
        assertThat(PrinterSelection.kickDecision(autoKickPref = true, isAutoPrint = true, serverKickAllowed = true)).isTrue()
    }

    @Test fun a_card_sale_never_kicks_even_with_the_pref_on() {
        assertThat(PrinterSelection.kickDecision(true, true, serverKickAllowed = false)).isFalse()
    }

    @Test fun a_manual_reprint_never_kicks_even_for_cash_with_the_pref_on() {
        assertThat(PrinterSelection.kickDecision(true, isAutoPrint = false, serverKickAllowed = true)).isFalse()
    }

    @Test fun the_pref_off_never_kicks() {
        assertThat(PrinterSelection.kickDecision(autoKickPref = false, isAutoPrint = true, serverKickAllowed = true)).isFalse()
    }

    @Test fun a_server_that_does_not_say_never_kicks() {
        // An older backend without kick_allowed: absent must mean no.
        assertThat(PrinterSelection.kickDecision(true, true, serverKickAllowed = null)).isFalse()
    }

    @Test fun the_receipt_model_json_without_kick_allowed_reads_as_null() {
        val m = com.google.gson.Gson().fromJson(
            """{"version":1,"lang":"en","rtl":false,"width_dots":576,"lines":[{"kind":"rule"}],"qr_payload":null}""",
            ReceiptModel::class.java)
        assertThat(m.kick_allowed).isNull()
        assertThat(PrinterSelection.kickDecision(true, true, m.kick_allowed)).isFalse()
    }

    @Test fun the_real_server_model_shape_parses_row_qty_and_kick() {
        val m = com.google.gson.Gson().fromJson(
            """{"version":1,"lang":"ar","rtl":true,"width_dots":384,"kick_allowed":true,"qr_payload":"Q",
               "lines":[{"kind":"text","text":"x","align":"center","bold":true,"size":"large"},
                        {"kind":"row","start":"a","end":"1.000","bold":false,"qty":"2x","strike":"2.000"},
                        {"kind":"qr","payload":"Q"}]}""",
            ReceiptModel::class.java)
        assertThat(m.rtl).isTrue()
        assertThat(m.kick_allowed).isTrue()
        assertThat(m.lines[1].qty).isEqualTo("2x")
        assertThat(m.lines[1].strike).isEqualTo("2.000")
        assertThat(m.lines[0].size).isEqualTo("large")
    }

    // ── built-in detection ────────────────────────────────────────────────
    @Test fun built_in_is_offered_only_on_sunmi_hardware_whose_service_resolves() {
        assertThat(PrinterSelection.builtInOffered("SUNMI", true)).isTrue()
        assertThat(PrinterSelection.builtInOffered("sunmi", true)).isTrue()
        assertThat(PrinterSelection.builtInOffered("SUNMI", false)).isFalse()   // Sunmi without the service
        assertThat(PrinterSelection.builtInOffered("samsung", true)).isFalse()  // package present by accident
        assertThat(PrinterSelection.builtInOffered(null, true)).isFalse()
    }

    // ── completeness per transport ────────────────────────────────────────
    @Test fun each_transport_needs_its_own_target() {
        fun ok(t: PrinterType, host: String = "", mac: String = "", vid: Int = -1, pid: Int = -1) =
            PrinterSelection.isComplete(t, host, mac, vid, pid)
        assertThat(ok(PrinterType.NONE, host = "1.2.3.4")).isFalse()
        assertThat(ok(PrinterType.NETWORK)).isFalse()
        assertThat(ok(PrinterType.NETWORK, host = "192.168.1.5")).isTrue()
        assertThat(ok(PrinterType.BLUETOOTH, mac = "")).isFalse()
        assertThat(ok(PrinterType.BLUETOOTH, mac = "not-a-mac")).isFalse()
        assertThat(ok(PrinterType.BLUETOOTH, mac = "00:11:22:33:44:55")).isTrue()
        assertThat(ok(PrinterType.USB, vid = 1208, pid = -1)).isFalse()
        assertThat(ok(PrinterType.USB, vid = 1208, pid = 514)).isTrue()
        assertThat(ok(PrinterType.BUILTIN)).isTrue()
    }

    @Test fun stored_type_keys_round_trip_and_unknown_keys_mean_none() {
        PrinterType.values().forEach { assertThat(PrinterType.fromKey(it.key)).isEqualTo(it) }
        assertThat(PrinterType.fromKey("hologram")).isEqualTo(PrinterType.NONE)
        assertThat(PrinterType.fromKey(null)).isEqualTo(PrinterType.NONE)
        // The persisted keys are a contract with installed phones.
        assertThat(PrinterType.values().map { it.key })
            .containsExactly("none", "network", "bluetooth", "usb", "builtin").inOrder()
    }

    // ── adapter selection door ────────────────────────────────────────────
    @Test fun a_bitmap_printer_gets_the_job_and_a_byte_printer_gets_the_bytes() = kotlinx.coroutines.runBlocking {
        var gotBytes: ByteArray? = null
        var gotJob: PrintJob? = null
        val bytePrinter = object : ReceiptPrinterAdapter {
            override val displayName = "bytes"
            override fun isSupportedOnThisDevice() = true
            override fun requiredPermissions() = emptyList<String>()
            override suspend fun print(bytes: ByteArray): Result<Unit> { gotBytes = bytes; return Result.success(Unit) }
        }
        val bitmapPrinter = object : ReceiptPrinterAdapter, BitmapPrinter {
            override val displayName = "bitmap"
            override fun isSupportedOnThisDevice() = true
            override fun requiredPermissions() = emptyList<String>()
            override suspend fun print(bytes: ByteArray): Result<Unit> = Result.failure(PrinterError("wrong door"))
            override suspend fun printJob(job: PrintJob): Result<Unit> { gotJob = job; return Result.success(Unit) }
        }
        val job = PrintJob(byteArrayOf(1, 2, 3), rendered = null, kick = false)
        assertThat(send(bytePrinter, job).isSuccess).isTrue()
        assertThat(gotBytes).isEqualTo(byteArrayOf(1, 2, 3))
        assertThat(send(bitmapPrinter, job).isSuccess).isTrue()
        assertThat(gotJob).isSameInstanceAs(job)
    }

    @Test fun the_bluetooth_spp_uuid_is_the_standard_serial_port_profile() {
        assertThat(BluetoothPrinterAdapter.SPP_UUID.toString()).isEqualTo("00001101-0000-1000-8000-00805f9b34fb")
        assertThat(BluetoothPrinterAdapter.CONNECT_TIMEOUT_MS).isEqualTo(5000L)
    }
}
