@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.actionaura.retail.ui.screens

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.actionaura.retail.ui.i18n.tr
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.common.InputImage
import java.util.concurrent.Executors

/**
 * Full-screen phone-camera barcode scanner for Aura POS.
 *
 * Uses CameraX for the live preview + ML Kit's BUNDLED barcode model (offline, no
 * Google Play Services needed). Fires [onResult] exactly once with the first decoded
 * barcode, then the caller dismisses. Camera permission is requested at runtime; if it
 * is denied the user still gets a manual-entry fallback so scanning is never a hard
 * dependency (mirrors the "optional enhancement" principle of the desktop build).
 */
@Composable
fun BarcodeScannerDialog(
    onResult: (String) -> Unit,
    onDismiss: () -> Unit,
    continuous: Boolean = false,        // keep scanning item after item (POS)
    statusText: String? = null,         // live "N items · $total" shown in continuous mode
) {
    val context = LocalContext.current
    var hasPermission by remember {
        mutableStateOf(
            ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) ==
                PackageManager.PERMISSION_GRANTED
        )
    }
    var permissionAsked by remember { mutableStateOf(false) }
    val permLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted -> hasPermission = granted; permissionAsked = true }

    LaunchedEffect(Unit) { if (!hasPermission) permLauncher.launch(Manifest.permission.CAMERA) }

    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false),
    ) {
        Surface(Modifier.fillMaxSize(), color = Color.Black) {
            Box(Modifier.fillMaxSize()) {
                if (hasPermission) {
                    CameraPreview(continuous = continuous, onBarcode = onResult)
                    // Aiming frame
                    Box(
                        Modifier.align(Alignment.Center).size(240.dp)
                            .border(2.dp, Color.White.copy(alpha = 0.85f), RoundedCornerShape(16.dp))
                    )
                    if (continuous) {
                        // Live running total + a Done button to finish and proceed to the cart.
                        Column(
                            Modifier.align(Alignment.BottomCenter).fillMaxWidth().padding(20.dp),
                            horizontalAlignment = Alignment.CenterHorizontally,
                        ) {
                            Text(
                                tr("Keep scanning — tap Done when finished"),
                                color = Color.White.copy(alpha = 0.85f), textAlign = TextAlign.Center,
                            )
                            Spacer(Modifier.height(10.dp))
                            if (!statusText.isNullOrBlank()) {
                                Surface(
                                    color = Color.Black.copy(alpha = 0.55f),
                                    shape = RoundedCornerShape(12.dp),
                                ) {
                                    Text(
                                        statusText, color = Color.White,
                                        style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold,
                                        modifier = Modifier.padding(horizontal = 16.dp, vertical = 10.dp),
                                    )
                                }
                                Spacer(Modifier.height(12.dp))
                            }
                            Button(
                                onClick = onDismiss,
                                modifier = Modifier.fillMaxWidth().height(54.dp),
                            ) { Text(tr("Done")) }
                        }
                    } else {
                        Text(
                            tr("Point the camera at a barcode"),
                            color = Color.White, textAlign = TextAlign.Center,
                            modifier = Modifier.align(Alignment.BottomCenter).padding(bottom = 120.dp)
                                .fillMaxWidth().padding(horizontal = 24.dp),
                        )
                    }
                } else {
                    ManualFallback(asked = permissionAsked, onResult = onResult)
                }

                // Close button (always available)
                FilledTonalIconButton(
                    onClick = onDismiss,
                    modifier = Modifier.align(Alignment.TopEnd).padding(16.dp),
                ) { Icon(Icons.Default.Close, contentDescription = tr("Close scanner")) }
            }
        }
    }
}

@Composable
private fun CameraPreview(continuous: Boolean, onBarcode: (String) -> Unit) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val analysisExecutor = remember { Executors.newSingleThreadExecutor() }
    val scanner = remember { BarcodeScanning.getClient() }
    // One-shot guard (single-scan mode): the first decoded value fires exactly once.
    val handled = remember { java.util.concurrent.atomic.AtomicBoolean(false) }
    // Continuous mode: debounce so the same code held in frame isn't added many times/sec.
    val lastCode = remember { java.util.concurrent.atomic.AtomicReference<String?>(null) }
    val lastAt = remember { java.util.concurrent.atomic.AtomicLong(0L) }
    // Hold the provider so we can release the camera when the scanner closes — otherwise
    // it stays bound to the Activity lifecycle (camera indicator on, battery drain).
    val providerHolder = remember { mutableStateOf<ProcessCameraProvider?>(null) }

    DisposableEffect(Unit) {
        onDispose {
            runCatching { providerHolder.value?.unbindAll() }
            analysisExecutor.shutdown()
            runCatching { scanner.close() }
        }
    }

    AndroidView(
        modifier = Modifier.fillMaxSize(),
        factory = { ctx ->
            val previewView = PreviewView(ctx).apply { scaleType = PreviewView.ScaleType.FILL_CENTER }
            val providerFuture = ProcessCameraProvider.getInstance(ctx)
            providerFuture.addListener({
                val provider = providerFuture.get()
                providerHolder.value = provider
                val preview = Preview.Builder().build().also {
                    it.setSurfaceProvider(previewView.surfaceProvider)
                }
                val analysis = ImageAnalysis.Builder()
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                    .build()
                analysis.setAnalyzer(analysisExecutor) { proxy ->
                    processFrame(scanner, proxy) { code ->
                        if (continuous) {
                            val now = android.os.SystemClock.elapsedRealtime()
                            val dup = com.actionaura.retail.barcode.isDuplicateScan(
                                code, lastCode.get(), lastAt.get(), now)
                            if (!dup) {
                                lastCode.set(code); lastAt.set(now)
                                ContextCompat.getMainExecutor(ctx).execute { onBarcode(code) }
                            }
                        } else if (handled.compareAndSet(false, true)) {
                            ContextCompat.getMainExecutor(ctx).execute { onBarcode(code) }
                        }
                    }
                }
                runCatching {
                    provider.unbindAll()
                    provider.bindToLifecycle(
                        lifecycleOwner, CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis,
                    )
                }
            }, ContextCompat.getMainExecutor(ctx))
            previewView
        },
    )
}

@androidx.annotation.OptIn(androidx.camera.core.ExperimentalGetImage::class)
private fun processFrame(
    scanner: com.google.mlkit.vision.barcode.BarcodeScanner,
    proxy: ImageProxy,
    onCode: (String) -> Unit,
) {
    val media = proxy.image
    if (media == null) { proxy.close(); return }
    val input = InputImage.fromMediaImage(media, proxy.imageInfo.rotationDegrees)
    scanner.process(input)
        .addOnSuccessListener { barcodes ->
            barcodes.firstOrNull { !it.rawValue.isNullOrBlank() }?.rawValue?.let(onCode)
        }
        .addOnCompleteListener { proxy.close() }
}

@Composable
private fun ManualFallback(asked: Boolean, onResult: (String) -> Unit) {
    var value by remember { mutableStateOf("") }
    Column(
        Modifier.fillMaxSize().padding(28.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            if (asked) tr("Camera permission denied") else tr("Camera unavailable"),
            color = Color.White, style = MaterialTheme.typography.titleMedium,
        )
        Spacer(Modifier.height(8.dp))
        Text(
            tr("You can still enter the barcode manually below."),
            color = Color.White.copy(alpha = 0.7f), textAlign = TextAlign.Center,
        )
        Spacer(Modifier.height(20.dp))
        OutlinedTextField(
            value = value, onValueChange = { value = it },
            label = { Text(tr("Barcode")) }, singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(16.dp))
        Button(
            onClick = { if (value.isNotBlank()) onResult(value.trim()) },
            enabled = value.isNotBlank(), modifier = Modifier.fillMaxWidth().height(50.dp),
        ) { Text(tr("Use this barcode")) }
    }
}
