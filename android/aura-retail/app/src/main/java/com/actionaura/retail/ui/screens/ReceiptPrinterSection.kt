package com.actionaura.retail.ui.screens

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.selection.toggleable
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.actionaura.retail.printer.BluetoothPrinterAdapter
import com.actionaura.retail.printer.PrinterPrefs
import com.actionaura.retail.printer.PrinterSelection
import com.actionaura.retail.printer.PrinterType
import com.actionaura.retail.printer.ReceiptPrintService
import com.actionaura.retail.printer.SunmiInnerPrinterAdapter
import com.actionaura.retail.printer.UsbPrinterAdapter
import com.actionaura.retail.ui.RetailSession
import com.actionaura.retail.ui.components.SectionHeader
import com.actionaura.retail.ui.i18n.AppLocale
import com.actionaura.retail.ui.i18n.tr
import kotlinx.coroutines.launch

/**
 * Settings -> Receipt Printer (PRINT-2): choose WHERE receipts go (built-in /
 * Bluetooth / USB / network), the paper width, auto-print, whether a cash sale
 * may open the drawer, plus a test print and (admin only) a drawer test.
 *
 * Costs an install that never opens it nothing: type defaults to none, and the
 * "Built-in" chip only exists on a Sunmi terminal whose printer service
 * resolves ([PrinterSelection.builtInOffered]).
 *
 * Bluetooth lists BONDED devices only -- pairing happens in Android's own
 * settings, so there is no scan UI and no location permission.
 */
@Composable
fun ReceiptPrinterSection(snackbar: SnackbarHostState) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()

    var type by remember { mutableStateOf(PrinterPrefs.getType(ctx)) }
    var host by remember { mutableStateOf(PrinterPrefs.getHost(ctx)) }
    var port by remember { mutableStateOf(PrinterPrefs.getPort(ctx).toString()) }
    var width by remember { mutableStateOf(PrinterPrefs.getWidth(ctx)) }
    var autoPrint by remember { mutableStateOf(PrinterPrefs.getAutoPrint(ctx)) }
    var autoKick by remember { mutableStateOf(PrinterPrefs.getAutoKick(ctx)) }
    var btMac by remember { mutableStateOf(PrinterPrefs.getBluetoothMac(ctx)) }
    var btName by remember { mutableStateOf(PrinterPrefs.getBluetoothName(ctx)) }
    var usbVid by remember { mutableStateOf(PrinterPrefs.getUsbVendorId(ctx)) }
    var usbPid by remember { mutableStateOf(PrinterPrefs.getUsbProductId(ctx)) }
    var usbName by remember { mutableStateOf(PrinterPrefs.getUsbName(ctx)) }
    var busy by remember { mutableStateOf(false) }

    val builtInAvailable = remember { SunmiInnerPrinterAdapter.isAvailable(ctx) }
    var bonded by remember { mutableStateOf(BluetoothPrinterAdapter.bondedDevices(ctx)) }
    var usbList by remember { mutableStateOf(UsbPrinterAdapter.listPrinters(ctx)) }
    val btPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) bonded = BluetoothPrinterAdapter.bondedDevices(ctx)
        else scope.launch { snackbar.showSnackbar(tr("Allow Nearby devices to list paired printers.")) }
    }

    fun needsBtPermission() = Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
        ContextCompat.checkSelfPermission(ctx, Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED

    fun pickType(t: PrinterType) {
        type = t
        if (t == PrinterType.BLUETOOTH) {
            if (needsBtPermission()) btPermission.launch(Manifest.permission.BLUETOOTH_CONNECT)
            else bonded = BluetoothPrinterAdapter.bondedDevices(ctx)
        }
        if (t == PrinterType.USB) usbList = UsbPrinterAdapter.listPrinters(ctx)
    }

    // Test buttons act on what is on screen, so save first: ReceiptPrintService
    // reads the saved prefs, and "I changed the printer and tapped Test" must
    // test the printer I just chose.
    fun save() {
        val portNum = port.toIntOrNull()?.takeIf { it in 1..65535 } ?: PrinterPrefs.DEFAULT_PORT
        PrinterPrefs.set(ctx, host, portNum, width, autoPrint)
        PrinterPrefs.setTransport(ctx, type, btMac, btName, usbVid, usbPid, usbName, autoKick)
        port = portNum.toString()
    }

    SectionHeader(tr("Receipt Printer"))
    Text(
        tr("Choose how this phone prints receipts. A Bluetooth printer must be paired in Android settings first."),
        style = MaterialTheme.typography.labelSmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )

    Text(tr("Printer type"), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        if (builtInAvailable) {
            FilterChip(selected = type == PrinterType.BUILTIN, onClick = { pickType(PrinterType.BUILTIN) },
                label = { Text(tr("Built-in")) })
        }
        FilterChip(selected = type == PrinterType.BLUETOOTH, onClick = { pickType(PrinterType.BLUETOOTH) },
            label = { Text(tr("Bluetooth")) })
        FilterChip(selected = type == PrinterType.USB, onClick = { pickType(PrinterType.USB) },
            label = { Text(tr("USB")) })
        FilterChip(selected = type == PrinterType.NETWORK, onClick = { pickType(PrinterType.NETWORK) },
            label = { Text(tr("Network")) })
    }

    when (type) {
        PrinterType.BUILTIN -> Text(tr("The built-in printer of this terminal."),
            style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        PrinterType.BLUETOOTH -> {
            Text(tr("Choose a paired printer"), fontWeight = FontWeight.Medium)
            if (bonded.isEmpty()) {
                Text(tr("No paired Bluetooth devices. Pair the printer in Android settings first."),
                    style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            bonded.forEach { d ->
                FilterChip(selected = btMac == d.mac, onClick = { btMac = d.mac; btName = d.name },
                    label = { Text("${d.name}  ${d.mac}") })
            }
            OutlinedButton(onClick = { pickType(PrinterType.BLUETOOTH) }) { Text(tr("Refresh")) }
        }
        PrinterType.USB -> {
            Text(tr("Choose a USB printer"), fontWeight = FontWeight.Medium)
            if (usbList.isEmpty()) {
                Text(tr("No USB printer is plugged in."),
                    style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            usbList.forEach { d ->
                val label = (d.productName ?: "USB printer") + "  ${d.vendorId}:${d.productId}"
                FilterChip(selected = usbVid == d.vendorId && usbPid == d.productId,
                    onClick = { usbVid = d.vendorId; usbPid = d.productId; usbName = d.productName ?: "" },
                    label = { Text(label) })
            }
            OutlinedButton(onClick = { usbList = UsbPrinterAdapter.listPrinters(ctx) }) { Text(tr("Refresh")) }
        }
        PrinterType.NETWORK -> {
            OutlinedTextField(host, { host = it },
                label = { Text(tr("Printer IP address")) }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(port, { port = it.filter(Char::isDigit) },
                label = { Text(tr("Port")) }, singleLine = true,
                keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                    keyboardType = androidx.compose.ui.text.input.KeyboardType.Number),
                modifier = Modifier.fillMaxWidth())
        }
        PrinterType.NONE -> Unit
    }

    Text(tr("Paper width"), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        FilterChip(selected = width == PrinterPrefs.WIDE_CHARS, onClick = { width = PrinterPrefs.WIDE_CHARS },
            label = { Text(tr("80mm (42 chars)")) })
        FilterChip(selected = width == PrinterPrefs.NARROW_CHARS, onClick = { width = PrinterPrefs.NARROW_CHARS },
            label = { Text(tr("58mm (32 chars)")) })
    }

    SwitchRow(tr("Auto-print after each sale"),
        tr("Prints automatically as soon as a sale completes, with no extra tap."), autoPrint) { autoPrint = it }
    SwitchRow(tr("Open the drawer after a cash sale"),
        tr("Only after a cash sale printed automatically. A reprint never opens it."), autoKick) { autoKick = it }

    Text(
        tr("Receipts print as a picture, so Arabic prints correctly. Always do a test print on your own printer before relying on it."),
        style = MaterialTheme.typography.labelSmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )

    Button(
        onClick = { save(); scope.launch { snackbar.showSnackbar(tr("Settings saved")) } },
        modifier = Modifier.fillMaxWidth().height(50.dp),
    ) { Text(tr("Save printer settings")) }

    OutlinedButton(
        enabled = !busy && PrinterSelection.isComplete(type, host, btMac, usbVid, usbPid),
        onClick = {
            save()
            scope.launch {
                busy = true
                ReceiptPrintService.printSample(ctx, AppLocale.isRtl)
                    .onSuccess { snackbar.showSnackbar(tr("Printed the test receipt")) }
                    .onFailure { snackbar.showSnackbar(it.message ?: tr("Couldn't print receipt")) }
                busy = false
            }
        },
        modifier = Modifier.fillMaxWidth().height(50.dp),
    ) { Text(tr("Test print")) }

    // Opening a till drawer moves cash access, so only an admin gets the button.
    if (RetailSession.isAdmin) {
        OutlinedButton(
            enabled = !busy && PrinterSelection.isComplete(type, host, btMac, usbVid, usbPid),
            onClick = {
                save()
                scope.launch {
                    busy = true
                    ReceiptPrintService.openDrawerTest(ctx)
                        .onSuccess { snackbar.showSnackbar(tr("Sent the drawer signal")) }
                        .onFailure { snackbar.showSnackbar(it.message ?: tr("Couldn't print receipt")) }
                    busy = false
                }
            },
            modifier = Modifier.fillMaxWidth().height(50.dp),
        ) { Text(tr("Open cash drawer (test)")) }
    }
}

/**
 * A labelled switch row. The Row carries toggleable + Role.Switch and the
 * Switch gets onCheckedChange = null -- the standard accessibility recipe, so
 * TalkBack announces what the switch toggles, not just "Switch, off".
 */
@Composable
private fun SwitchRow(title: String, hint: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(vertical = 4.dp)
            .toggleable(value = checked, role = Role.Switch, onValueChange = onChange),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, fontWeight = FontWeight.Medium)
            Text(hint, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Switch(checked = checked, onCheckedChange = null)
    }
}
