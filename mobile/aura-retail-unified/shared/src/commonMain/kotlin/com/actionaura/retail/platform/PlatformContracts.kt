package com.actionaura.retail.platform

/**
 * Every platform-specific capability the shared core needs, as a behavioral
 * contract with zero business logic in it. commonMain code depends only on
 * these interfaces, never on androidMain/iosMain types directly -- the
 * inverse dependency (commonMain -> platform) is what keeps
 * domain/financial/usecases genuinely platform-free.
 *
 * Real implementations live in shared/src/androidMain (Milestone 2 for the
 * skeleton, later milestones for full behavior) and shared/src/iosMain
 * (written as real Kotlin/Native code throughout this initiative even
 * though it cannot be compiled/linked/run on this Windows development host
 * -- see docs/retail/unified_mobile/ios-build-readiness-plan.md).
 */

/** Android Keystore / iOS Keychain-Secure Enclave -- Milestone 10. */
interface SecureCredentialStore {
    suspend fun put(key: String, value: ByteArray)
    suspend fun get(key: String): ByteArray?
    suspend fun delete(key: String)
    suspend fun contains(key: String): Boolean
}

/** Per-device installation identity keypair -- Milestones 7-10. */
interface InstallationIdentityProvider {
    suspend fun currentInstallationId(): String?
    suspend fun ensureInstallationKeypair(): InstallationKeypairHandle
}

class InstallationKeypairHandle(val publicKeyBase64: String)

/**
 * SQLDelight driver factory -- Milestone 4, real (not a placeholder
 * anymore): shared-database-schema-decision.md's evidence-based choice.
 * Returns the real app.cash.sqldelight `SqlDriver` type -- every
 * implementation (Android's `AndroidSqliteDriver`, iOS's future
 * `NativeSqliteDriver`) must open the connection with the exact real
 * pragmas android-database-audit.md documents from the Python authority:
 * journal_mode=WAL, busy_timeout=30000, foreign_keys=ON (per-connection,
 * every time -- SQLite does not persist this setting in the file itself).
 */
interface DatabaseDriverFactory {
    fun createDriver(): app.cash.sqldelight.db.SqlDriver
}

/** Continuous camera barcode scanning -- Milestone 14. */
interface CameraBarcodeScanner {
    fun start(onResult: (barcode: String) -> Unit, onError: (ScannerError) -> Unit)
    fun stop()
    fun isAvailable(): Boolean
}

/** External HID (keyboard-wedge) barcode scanner input -- Milestone 14. */
interface ExternalScannerInput {
    fun observe(onResult: (barcode: String) -> Unit)
    fun dispose()
}

enum class ScannerError { PERMISSION_DENIED, CAMERA_UNAVAILABLE, HARDWARE_ERROR, UNKNOWN }

/** Platform print pipeline (AirPrint / Android print framework) -- Milestone 15. */
interface ReceiptPrinter {
    suspend fun print(receipt: RenderedReceipt): PrintResult
    fun isSupported(): Boolean
}

class RenderedReceipt(val bytesPdf: ByteArray, val plainText: String)
enum class PrintResult { SUCCESS, CANCELLED, UNSUPPORTED, FAILED }

/** Platform share sheet -- Milestone 15. */
interface ReceiptShareService {
    suspend fun share(receipt: RenderedReceipt, suggestedFileName: String)
}

/** File selection for Import Center / restore -- Milestones 4, 16, complete-capability Import Center. */
interface FilePicker {
    suspend fun pickFile(allowedExtensions: List<String>): PickedFile?
}

class PickedFile(val name: String, val bytes: ByteArray)

/** Backup artifact storage -- Milestone 16. */
interface BackupStorage {
    suspend fun writeBackup(fileName: String, bytes: ByteArray): String // returns platform-appropriate location descriptor
    suspend fun listBackups(): List<BackupDescriptor>
}

class BackupDescriptor(val fileName: String, val createdAtEpochMillis: Long, val sizeBytes: Long)

/** Backup restoration source -- Milestone 16. */
interface RestoreStorage {
    suspend fun readBackup(descriptor: BackupDescriptor): ByteArray
}

/** Connectivity signal for the offline-first lease/refresh logic -- Milestone 11. */
interface NetworkStatus {
    fun isOnline(): Boolean
    fun observe(onChange: (online: Boolean) -> Unit)
}

/** App foreground/background lifecycle -- Milestone 11 (lease refresh timing). */
interface AppLifecycle {
    fun observe(onForeground: () -> Unit, onBackground: () -> Unit)
}

/** Scheduled background lease-refresh -- Milestone 11. */
interface BackgroundRefresh {
    fun schedule(intervalMinutes: Long, task: suspend () -> Unit)
    fun cancel()
}

/** Clock-rollback risk signal for offline lease verification -- Milestone 11. */
interface ClockIntegritySignal {
    fun suspectedRollback(): Boolean
}

/** Bounded, privacy-safe device metadata (never raw hardware identifiers) -- Milestone 7-10. */
interface DeviceInfoProvider {
    fun platformName(): String // "ANDROID" | "IOS"
    fun osVersion(): String
    fun deviceDisplayModel(): String // e.g. "Pixel 8" / "iPhone 15" -- never IMEI/UDID
}

/** App version reporting for the release manifest / minimum-version gate -- Milestone 26. */
interface AppVersionProvider {
    fun versionName(): String
    fun buildNumber(): String
}

/** Deep-link routing (e.g. from a licensing renewal notification) -- Milestone 19+ scope. */
interface DeepLinkHandler {
    fun parse(url: String): DeepLinkTarget?
}

sealed interface DeepLinkTarget

/** OS permission request/check -- Milestone 14 (camera) primarily. */
interface PermissionManager {
    suspend fun isGranted(permission: PlatformPermission): Boolean
    suspend fun request(permission: PlatformPermission): Boolean
}

enum class PlatformPermission { CAMERA }

/** Crash-report redaction boundary -- Milestone 20 (never leak secrets/business data to a crash report). */
interface CrashRedaction {
    fun recordSafe(throwable: Throwable, redactedContext: Map<String, String>)
}

/** Structured logging sink -- Milestone 20/26 (no secrets, no PII, ever). */
interface LoggingSink {
    fun log(level: LogLevel, tag: String, message: String)
}

enum class LogLevel { DEBUG, INFO, WARN, ERROR }
