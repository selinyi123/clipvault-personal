package com.clipvault.app.sync

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import com.clipvault.app.data.AppDatabase
import com.clipvault.app.data.ClipEntity
import com.clipvault.app.data.MemoryPrivacy
import com.clipvault.core.SecretGuard
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL
import java.security.KeyStore
import java.util.Locale
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

private const val SYNC_PREFS = "clipvault_sync"
private const val LEGACY_TOKEN = "token"
private const val TOKEN_PREFS = "clipvault_sync_token"
private const val TOKEN_KEY_ALIAS = "clipvault_sync_token_v1"
private const val TOKEN_IV = "token_iv"
private const val TOKEN_CT = "token_ct"
internal const val MAX_SYNC_RESPONSE_BYTES = 4 * 1024 * 1024
private val HOST_LABEL_RE = Regex("""^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$""")
private val BRACKETED_IPV6_RE = Regex("""^\[[0-9A-Fa-f:.%]+]$""")

internal class SyncAuthException : IOException("sync auth rejected")

internal fun isPermanentSyncAuthFailure(statusCode: Int): Boolean =
    statusCode == HttpURLConnection.HTTP_UNAUTHORIZED ||
        statusCode == HttpURLConnection.HTTP_FORBIDDEN

internal fun normalizeSyncHostOrNull(raw: String?): String? {
    val host = raw?.trim() ?: return null
    if (host.isEmpty() || host.length > 253) return null
    if (host.any { it <= ' ' || it.code == 127 }) return null
    if (host.any { it == '/' || it == '\\' || it == '?' || it == '#' || it == '@' }) return null

    if (host.startsWith("[") || host.endsWith("]")) {
        if (!BRACKETED_IPV6_RE.matches(host)) return null
        val inner = host.substring(1, host.length - 1)
        if (!inner.contains(":")) return null
        return host
    }

    if (host.contains(":")) return null
    val labels = host.split(".")
    if (labels.any { it.isEmpty() || !HOST_LABEL_RE.matches(it) }) return null
    return host.lowercase(Locale.ROOT)
}

internal fun readUtf8BodyBounded(input: InputStream, maxBytes: Int = MAX_SYNC_RESPONSE_BYTES): String {
    require(maxBytes > 0) { "maxBytes must be positive" }
    val out = ByteArrayOutputStream()
    val buffer = ByteArray(8192)
    var total = 0
    while (true) {
        val n = input.read(buffer)
        if (n == -1) break
        total += n
        if (total > maxBytes) throw IOException("response body too large")
        out.write(buffer, 0, n)
    }
    return out.toString(Charsets.UTF_8.name())
}

/** Keystore-backed bearer-token storage.
 *
 * The sync bearer token authorizes pull/push access to public ClipVault data, so
 * it should not live as plaintext SharedPreferences. Host/port/cursor remain in
 * ordinary prefs; only the token is encrypted with an AndroidKeyStore AES-GCM key.
 */
private class SecureTokenStore(context: Context) {
    private val ctx = context.applicationContext
    private val sp = ctx.getSharedPreferences(TOKEN_PREFS, Context.MODE_PRIVATE)

    fun get(): String? {
        val ivB64 = sp.getString(TOKEN_IV, null) ?: return null
        val ctB64 = sp.getString(TOKEN_CT, null) ?: return null
        return try {
            val iv = Base64.decode(ivB64, Base64.NO_WRAP)
            val ct = Base64.decode(ctB64, Base64.NO_WRAP)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, iv))
            String(cipher.doFinal(ct), Charsets.UTF_8)
        } catch (e: Exception) {
            // If the keystore entry was invalidated/corrupted, fail closed and
            // require pairing again rather than returning stale plaintext.
            sp.edit().remove(TOKEN_IV).remove(TOKEN_CT).apply()
            android.util.Log.w("clipvault.sync", "token decrypt failed: ${e.javaClass.simpleName}")
            null
        }
    }

    fun set(value: String?) {
        if (value.isNullOrEmpty()) {
            sp.edit().remove(TOKEN_IV).remove(TOKEN_CT).apply()
            return
        }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val ct = cipher.doFinal(value.toByteArray(Charsets.UTF_8))
        sp.edit()
            .putString(TOKEN_IV, Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
            .putString(TOKEN_CT, Base64.encodeToString(ct, Base64.NO_WRAP))
            .apply()
    }

    private fun key(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        if (!ks.containsAlias(TOKEN_KEY_ALIAS)) {
            val kg = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
            val spec = KeyGenParameterSpec.Builder(
                TOKEN_KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
                .build()
            kg.init(spec)
            kg.generateKey()
        }
        return (ks.getEntry(TOKEN_KEY_ALIAS, null) as KeyStore.SecretKeyEntry).secretKey
    }
}

/** Device-local sync settings. */
class Settings(context: Context) {
    private val appCtx = context.applicationContext
    private val sp = appCtx.getSharedPreferences(SYNC_PREFS, Context.MODE_PRIVATE)
    private val tokenStore = SecureTokenStore(appCtx)

    init { migrateLegacyToken() }

    var host: String?  get() = sp.getString("host", null);  set(v) { sp.edit().putString("host", v).apply() }
    var port: Int      get() = sp.getInt("port", 8787);     set(v) { sp.edit().putInt("port", v).apply() }
    var token: String? get() = tokenStore.get();             set(v) { tokenStore.set(v); sp.edit().remove(LEGACY_TOKEN).apply() }
    var sinceSeq: Long get() = sp.getLong("since", 0);      set(v) { sp.edit().putLong("since", v).apply() }
    val deviceId: String
        get() = sp.getString("device_id", null) ?: run {
            val id = "android-" + UUID.randomUUID().toString().take(8)
            sp.edit().putString("device_id", id).apply(); id
        }

    /** Commit a new host/token pairing without ever exposing a stale or fresh
     * token to the wrong host. Preferences and AndroidKeyStore are separate
     * stores, so the safest order is fail-closed: clear token -> synchronously
     * write host -> write new token. A concurrent worker can then see old
     * host+old token, new host+no token, or new host+new token, but never new
     * host+old token or old host+new token. */
    fun replacePairing(host: String, token: String) {
        tokenStore.set(null)
        val hostStored = sp.edit().putString("host", host).remove(LEGACY_TOKEN).commit()
        if (!hostStored) {
            tokenStore.set(null)
            throw IOException("pairing state write failed")
        }
        tokenStore.set(token)
    }

    /** One-time v1.2.x -> v1.3 migration. Preserve pairing while deleting the
     * old plaintext token from the legacy sync preference file. */
    private fun migrateLegacyToken() {
        val legacy = sp.getString(LEGACY_TOKEN, null)
        if (!legacy.isNullOrEmpty() && tokenStore.get().isNullOrEmpty()) {
            tokenStore.set(legacy)
        }
        if (legacy != null) sp.edit().remove(LEGACY_TOKEN).apply()
    }
}

/** Minimal HTTP sync client (SYNC-2). Uses HttpURLConnection — no extra deps. */
class SyncClient(private val s: Settings, private val hostOverride: String? = null) {
    private fun base(): String {
        val host = normalizeSyncHostOrNull(hostOverride ?: s.host) ?: throw IOException("invalid sync host")
        return "http://$host:${s.port}/api"
    }

    private fun req(method: String, path: String, body: String?, auth: Boolean): Pair<Int, String> {
        val bodyBytes = body?.toByteArray(Charsets.UTF_8)
        val c = (URL(base() + path).openConnection() as HttpURLConnection).apply {
            // ClipVault sync endpoints never redirect. Keep bearer tokens scoped
            // to the paired host instead of following a 3xx to another URL.
            instanceFollowRedirects = false
            requestMethod = method
            connectTimeout = 8000; readTimeout = 12000
            if (auth) s.token?.let { setRequestProperty("Authorization", "Bearer $it") }
            if (bodyBytes != null) {
                doOutput = true
                setRequestProperty("Content-Type", "application/json")
                setFixedLengthStreamingMode(bodyBytes.size)
            }
        }
        try {
            if (bodyBytes != null) c.outputStream.use { it.write(bodyBytes) }
            val code = c.responseCode
            val stream = if (code in 200..299) c.inputStream else c.errorStream
            val text = stream?.use { readUtf8BodyBounded(it) } ?: ""
            return code to text
        } finally {
            c.disconnect()
        }
    }

    /** Redeem a one-time pairing code shown on the desktop Web UI.
     * Never throws: any network/parse failure returns false so the UI shows a
     * "配对失败" message instead of crashing the app. */
    fun pair(code: String): Boolean {
        return try {
            val token = requestPairToken(code) ?: return false
            s.token = token
            true
        } catch (e: Exception) {
            android.util.Log.w("clipvault.sync", "pair failed: ${e.javaClass.simpleName}")
            false
        }
    }

    /** Pair against a user-entered host without committing that host until the
     * desktop returns a fresh token. This prevents an existing old token from
     * being sent to a mistyped or malicious replacement host by a background
     * sync worker after a failed pairing attempt. */
    fun pairWithHost(host: String, code: String): Boolean {
        return try {
            val h = normalizeSyncHostOrNull(host) ?: return false
            val token = SyncClient(s, h).requestPairToken(code) ?: return false
            s.replacePairing(h, token)
            true
        } catch (e: Exception) {
            android.util.Log.w("clipvault.sync", "pair failed: ${e.javaClass.simpleName}")
            false
        }
    }

    private fun requestPairToken(code: String): String? {
        val body = JSONObject().put("code", code).put("device_id", s.deviceId)
            .put("device_name", android.os.Build.MODEL ?: "Android").toString()
        val (code2, text) = req("POST", "/pair", body, auth = false)
        if (code2 != 200) return null
        val token = JSONObject(text).optString("token", "")
        return token.ifEmpty { null }
    }

    fun push(events: JSONArray): Long {
        val body = JSONObject().put("events", events).toString()
        val (code, text) = req("POST", "/sync/push", body, auth = true)
        if (code == 200) return JSONObject(text).optLong("acked_upto", 0)
        if (isPermanentSyncAuthFailure(code)) throw SyncAuthException()
        return -1
    }

    fun pull(since: Long): JSONObject? {
        val (code, text) = req("GET", "/sync/pull?since_seq=$since", null, auth = true)
        if (code == 200) return JSONObject(text)
        if (isPermanentSyncAuthFailure(code)) throw SyncAuthException()
        return null
    }
}

/** Apply events pulled from the desktop into the local cache. Does NOT touch
 * the outbox (no echo). Gate A re-scan on arrival. */
object SyncApply {
    fun applyEvents(db: AppDatabase, events: JSONArray) {
        for (i in 0 until events.length()) {
            val ev = events.optJSONObject(i)
            if (ev == null) {
                android.util.Log.w("clipvault.sync", "ignored malformed event")
                continue
            }
            try {
                when (ev.optString("kind", "")) {
                    "clip_new" -> applyClipNew(db, ev.getJSONObject("payload"))
                    "clip_meta" -> applyClipMeta(db, ev.getJSONObject("payload"))
                    "memory_upsert" -> applyMemoryUpsert(db, ev.getJSONObject("payload"))
                    "memory_delete" -> applyMemoryDelete(db, ev.getJSONObject("payload"))
                    else -> android.util.Log.w("clipvault.sync", "ignored unknown event kind")
                }
            } catch (e: org.json.JSONException) {
                // A malformed payload (missing/mistyped fields) is permanently bad,
                // so skip it instead of retrying the batch forever. Anything else
                // (e.g. a transient DB write failure) is deliberately NOT caught
                // here: it propagates so SyncWorker retries without advancing the
                // sync cursor past an event we have not actually applied.
                android.util.Log.w("clipvault.sync", "ignored malformed event: ${e.javaClass.simpleName}")
            }
        }
    }

    private fun applyClipNew(db: AppDatabase, d: JSONObject) {
        val hash = d.getString("content_hash")
        if (db.clips().byHash(hash) != null) return
        val content = d.getString("content")
        val verdict = SecretGuard.scan(content)              // gate A
        val isSecret = verdict.isSecret || d.optBoolean("is_secret", false)
        db.clips().insert(
            ClipEntity(
                id = d.getString("id"), content = content, contentHash = hash,
                contentType = d.getString("content_type"),
                isSecret = isSecret, secretLevel = if (isSecret) verdict.level else null,
                secretReasons = JSONArray(verdict.reasons).toString(),
                sourceDevice = d.optString("source_device", "desktop"),
                sourceApp = if (d.isNull("source_app")) null else d.optString("source_app"),
                createdAt = d.getString("created_at"), lastSeenAt = d.getString("last_seen_at"),
                timesSeen = d.optInt("times_seen", 1),
                pinned = d.optBoolean("pinned", false), favorite = d.optBoolean("favorite", false),
                deleted = d.optBoolean("deleted", false),
            )
        )
    }

    private fun applyClipMeta(db: AppDatabase, d: JSONObject) {
        val hash = d.getString("content_hash")
        val clip = db.clips().byHash(hash) ?: return
        val patch = d.getJSONObject("patch")
        if (patch.has("pinned")) db.clips().setPinnedByHash(hash, patch.getBoolean("pinned"))
        if (patch.has("favorite")) db.clips().setFavoriteByHash(hash, patch.getBoolean("favorite"))
        if (patch.optBoolean("deleted", false)) db.clips().softDelete(clip.id)
    }

    private fun applyMemoryUpsert(db: AppDatabase, d: JSONObject) {
        val text = d.getString("text")
        val label = if (d.isNull("label")) null else d.optString("label")
        if (MemoryPrivacy.containsSecret(text, label)) {
            // Gate A on arrival, mirroring clip_new. Never log the payload.
            android.util.Log.w("clipvault.sync", "ignored secret memory event")
            return
        }
        db.memory().upsert(
            com.clipvault.app.data.MemoryEntity(
                kind = d.getString("kind"), text = text,
                label = label,
                pinned = d.optBoolean("pinned", false),
                useCount = d.optInt("use_count", 0),
                source = d.optString("source", "manual"),
            )
        )
    }

    private fun applyMemoryDelete(db: AppDatabase, d: JSONObject) {
        db.memory().softDelete(d.getString("kind"), d.getString("text"))
    }
}
