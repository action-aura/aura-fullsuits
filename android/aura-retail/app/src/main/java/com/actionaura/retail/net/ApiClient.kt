package com.actionaura.retail.net

import android.content.Context
import android.content.SharedPreferences
import com.actionaura.retail.server.ServerBootstrap
import okhttp3.Cookie
import okhttp3.CookieJar
import okhttp3.HttpUrl
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit

/**
 * Retrofit/OkHttp client for the embedded server. Persists the Flask session cookie
 * to SharedPreferences so the login survives app restarts (testers stay signed in
 * instead of hitting the login screen on every cold start). Built lazily after the
 * server port is known. Call init(context) once at startup to enable persistence.
 */
object ApiClient {

    private const val PREFS = "aura_cookies"
    private const val KEY = "cookies"

    @Volatile private var prefs: SharedPreferences? = null
    private val store = mutableMapOf<String, MutableList<Cookie>>()
    @Volatile private var loaded = false

    /** Wire up persistent cookie storage. Safe to call more than once. */
    fun init(context: Context) {
        if (prefs == null) {
            prefs = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            loadFromPrefs()
        }
    }

    @Synchronized
    private fun loadFromPrefs() {
        if (loaded) return
        loaded = true
        val raw = prefs?.getStringSet(KEY, emptySet()) ?: return
        for (line in raw) {
            val idx = line.indexOf('\n'); if (idx < 0) continue
            val host = line.substring(0, idx)
            val cstr = line.substring(idx + 1)
            val url = HttpUrl.Builder().scheme("http").host(host).build()
            val c = Cookie.parse(url, cstr) ?: continue
            store.getOrPut(host) { mutableListOf() }.add(c)
        }
    }

    @Synchronized
    private fun persist() {
        val p = prefs ?: return
        val now = System.currentTimeMillis()
        val set = mutableSetOf<String>()
        for ((host, list) in store) for (c in list) {
            if (c.persistent && c.expiresAt > now) set.add("$host\n$c")
        }
        p.edit().putStringSet(KEY, set).apply()
    }

    private val cookieJar = object : CookieJar {
        @Synchronized
        override fun saveFromResponse(url: HttpUrl, cookies: List<Cookie>) {
            loadFromPrefs()
            val list = store.getOrPut(url.host) { mutableListOf() }
            for (c in cookies) {
                list.removeAll { it.name == c.name }
                list.add(c)
            }
            persist()
        }

        @Synchronized
        override fun loadForRequest(url: HttpUrl): List<Cookie> {
            loadFromPrefs()
            val now = System.currentTimeMillis()
            return store[url.host]?.filter { it.expiresAt > now } ?: emptyList()
        }
    }

    @Volatile private var api: AuraApi? = null

    fun get(): AuraApi {
        api?.let { return it }
        synchronized(this) {
            api?.let { return it }
            val client = OkHttpClient.Builder()
                .cookieJar(cookieJar)
                .connectTimeout(15, TimeUnit.SECONDS)
                .readTimeout(30, TimeUnit.SECONDS)
                .build()
            val retrofit = Retrofit.Builder()
                .baseUrl(ServerBootstrap.baseUrl())
                .client(client)
                .addConverterFactory(GsonConverterFactory.create())
                .build()
            return retrofit.create(AuraApi::class.java).also { api = it }
        }
    }
}
