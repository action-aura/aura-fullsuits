package com.actionaura.retail.platform

import android.content.Context
import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Log
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import java.security.KeyStore
import javax.crypto.KeyGenerator

/**
 * Real Android implementations of the Milestone 2 platform contracts.
 * Business logic stays zero here -- these are pure OS-adapter classes,
 * matching the "no business rules in platform implementations" architecture
 * requirement.
 */

/** Android Keystore-backed secret storage (Milestone 10's real target;
 * this is the Milestone 2 skeleton wiring -- EncryptedSharedPreferences
 * with a Keystore-backed MasterKey, the standard AndroidX-recommended
 * pattern, not a placeholder). */
class AndroidSecureCredentialStore(context: Context) : SecureCredentialStore {
    private val masterKey = MasterKey.Builder(context)
        .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
        .build()

    private val prefs = EncryptedSharedPreferences.create(
        context,
        "aura_retail_secure_credentials",
        masterKey,
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    override suspend fun put(key: String, value: ByteArray) {
        prefs.edit().putString(key, android.util.Base64.encodeToString(value, android.util.Base64.NO_WRAP)).apply()
    }

    override suspend fun get(key: String): ByteArray? {
        val encoded = prefs.getString(key, null) ?: return null
        return android.util.Base64.decode(encoded, android.util.Base64.NO_WRAP)
    }

    override suspend fun delete(key: String) {
        prefs.edit().remove(key).apply()
    }

    override suspend fun contains(key: String): Boolean = prefs.contains(key)
}

class AndroidDeviceInfoProvider(private val context: Context) : DeviceInfoProvider {
    override fun platformName(): String = "ANDROID"
    override fun osVersion(): String = Build.VERSION.RELEASE ?: "unknown"
    override fun deviceDisplayModel(): String = "${Build.MANUFACTURER} ${Build.MODEL}".trim()
}

class AndroidAppVersionProvider(
    private val versionNameValue: String,
    private val versionCodeValue: Int,
) : AppVersionProvider {
    override fun versionName(): String = versionNameValue
    override fun buildNumber(): String = versionCodeValue.toString()
}

class AndroidLoggingSink : LoggingSink {
    override fun log(level: LogLevel, tag: String, message: String) {
        when (level) {
            LogLevel.DEBUG -> Log.d(tag, message)
            LogLevel.INFO -> Log.i(tag, message)
            LogLevel.WARN -> Log.w(tag, message)
            LogLevel.ERROR -> Log.e(tag, message)
        }
    }
}

class AndroidNetworkStatus(private val context: Context) : NetworkStatus {
    override fun isOnline(): Boolean {
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as android.net.ConnectivityManager
        val network = cm.activeNetwork ?: return false
        val caps = cm.getNetworkCapabilities(network) ?: return false
        return caps.hasCapability(android.net.NetworkCapabilities.NET_CAPABILITY_INTERNET)
    }

    override fun observe(onChange: (online: Boolean) -> Unit) {
        // Real NetworkCallback registration is Milestone 11 scope (lease
        // refresh timing) -- this skeleton only proves isOnline() works.
    }
}
