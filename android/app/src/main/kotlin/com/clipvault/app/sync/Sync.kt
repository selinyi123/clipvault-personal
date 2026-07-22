package com.clipvault.app.sync

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import com.clipvault.app.ClipVaultApp
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
private const val LEGACY_TOKEN_MIGRATED = "token_migrated_v1"
private const val TOKEN_PREFS = "clipvault_sync_token"
private const val TOKEN_KEY_ALIAS = "clipvault_sync_token_v1"
private const val TOKEN_IV = "token_iv"
private const val TOKEN_CT = "token_ct"
private const val PUSH_BLOCKED_SEQ = "push_blocked_seq"
private const val PUSH_BLOCKED_REASON = "push_blocked_reason"
private const val SERVER_DEVICE = "server_device"
internal const val MAX_SYNC_RESPONSE_BYTES = 7 * 1024 * 1024
private val HOST_LABEL_RE = Regex("""^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$""")
private val BRACKETED_IPV6_RE = Regex("""^\[[0-9A-Fa-f:.%]+]$""")

internal class SyncAuthException : IOException("sync auth rejected")
internal class SyncPairingChangedException : IOException("sync pairing changed")
internal class SyncPushRequestTooLargeException : IOException("sync push request rejected as too large")

internal enum class SyncPushBlockReason(val code: String, val safeMessage: String) {
    EVENT_TOO_LARGE("event_too_large", "sync push event exceeds request budget"),
    INVALID_PAYLOAD("invalid_payload", "sync push event payload is invalid"),
    ACK_OUT_OF_RANGE("ack_out_of_range", "sync push acknowledgement exceeds sent prefix");

    companion object {
        fun fromCode(code: String?): SyncPushBlockReason? = values().firstOrNull { it.code == code }
    }
}

internal data class SyncPushBlockedState(
    val seq: Long,
    val reason: SyncPushBlockReason,
)

internal fun isPermanentSyncAuthFailure(statusCode: Int): Boolean =
    statusCode == HttpURLConnection.HTTP_UNAUTHORIZED ||
        statusCode == HttpURLConnection.HTTP_FORBIDDEN

internal fun shouldReadSyncResponseBody(statusCode: Int, auth: Boolean): Boolean =
    !auth || !isPermanentSyncAuthFailure(statusCode)

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

/** A nullable value means the recognized field was absent. Explicit false is
 * retained so remote metadata can clear flags (including deleted). */
internal data class ClipMetaPatch(
    val pinned: Boolean?,
    val favorite: Boolean?,
    val deleted: Boolean?,
) {
    val isEmpty: Boolean
        get() = pinned == null && favorite == null && deleted == null
}

/** Parse every recognized field before SyncApply performs a database write.
 * Unknown fields are ignored for forward compatibility, while JSON null and
 * non-Boolean values for known fields are rejected without coercion. */
internal fun parseClipMetaPatch(patch: JSONObject): ClipMetaPatch = ClipMetaPatch(
    pinned = patch.strictOptionalBoolean("pinned"),
    favorite = patch.strictOptionalBoolean("favorite"),
    deleted = patch.strictOptionalBoolean("deleted"),
)

private fun JSONObject.strictOptionalBoolean(name: String): Boolean? {
    if (!has(name)) return null
    return get(name) as? Boolean
        ?: throw org.json.JSONException("$name must be a Boolean")
}

/** Pairing cursor negotiation is security/data-loss sensitive. JSON helpers
 * such as optLong coerce strings, booleans, and floating-point values, so only
 * actual positive integral JSON number representations are accepted.
 */
internal fun strictPairingBaseSeq(value: Any?): Long? {
    val parsed = when (value) {
        is Int -> value.toLong()
        is Long -> value
        else -> return null
    }
    return parsed.takeIf { it > 0L }
}

/** Validated subset of a pairing response. This deliberately is not a data
 * class so accidental logging cannot include the bearer token through a
 * generated toString().
 */
internal class ValidatedPairingResponse(
    val token: String,
    val serverDevice: String?,
)

internal fun parsePairingResponse(
    text: String,
    expectedOutboxBaseSeq: Long,
): ValidatedPairingResponse? {
    val parsed = JSONObject(text)
    if (strictPairingBaseSeq(parsed.opt("outbox_base_seq")) != expectedOutboxBaseSeq) {
        return null
    }
    val token = (parsed.opt("token") as? String)?.takeIf { it.isNotEmpty() } ?: return null
    val serverDevice = when (val value = parsed.opt("server_device")) {
        null, JSONObject.NULL -> null
        is String -> value.ifEmpty { null }
        else -> return null
    }
    return ValidatedPairingResponse(token, serverDevice)
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
            val cleared = sp.edit().remove(TOKEN_IV).remove(TOKEN_CT).commit()
            if (!cleared) throw IOException("token clear failed")
            return
        }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val ct = cipher.doFinal(value.toByteArray(Charsets.UTF_8))
        val stored = sp.edit()
            .putString(TOKEN_IV, Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
            .putString(TOKEN_CT, Base64.encodeToString(ct, Base64.NO_WRAP))
            .commit()
        if (!stored) throw IOException("token write failed")
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
    private val pairingGate = SyncPairingStateGate()

    init {
        // The first Settings instance performs the one-time migration before
        // any request can exist. Later constructions must not advance the
        // request revision merely by reading already-migrated storage.
        pairingGate.read { migrateLegacyToken() }
    }

    var host: String?
        get() = pairingGate.read { sp.getString("host", null) }
        set(v) {
            pairingGate.replaceEndpoint {
                val endpointChanged =
                    normalizeSyncHostOrNull(sp.getString("host", null)) != normalizeSyncHostOrNull(v)
                clearStoredToken()
                val editor = sp.edit().putString("host", v).remove(LEGACY_TOKEN)
                if (endpointChanged) editor.putLong("since", 0L).remove(SERVER_DEVICE)
                val stored = editor.commit()
                if (!stored) throw IOException("sync host write failed")
            }
        }
    var port: Int
        get() = pairingGate.read { sp.getInt("port", 8787) }
        set(v) {
            pairingGate.replaceEndpoint {
                val endpointChanged = sp.getInt("port", 8787) != v
                clearStoredToken()
                val editor = sp.edit().putInt("port", v).remove(LEGACY_TOKEN)
                if (endpointChanged) editor.putLong("since", 0L).remove(SERVER_DEVICE)
                val stored = editor.commit()
                if (!stored) throw IOException("sync port write failed")
            }
        }
    var token: String?
        get() = pairingGate.read { tokenStore.get() }
        set(v) {
            pairingGate.replace {
                clearStoredToken()
                if (!v.isNullOrEmpty()) installFreshToken(v)
            }
        }
    /** Direct setter remains for compatibility; pull responses use the CAS
     * method below so a stale page cannot advance or overwrite this cursor. */
    var sinceSeq: Long
        get() = pairingGate.read { sp.getLong("since", 0L) }
        set(v) {
            pairingGate.read {
                if (!sp.edit().putLong("since", v).commit()) {
                    throw IOException("sync cursor write failed")
                }
            }
        }
    internal val syncPushBlocked: SyncPushBlockedState?
        get() {
            val seq = sp.getLong(PUSH_BLOCKED_SEQ, 0L)
            val reason = SyncPushBlockReason.fromCode(sp.getString(PUSH_BLOCKED_REASON, null))
            return if (seq > 0L && reason != null) SyncPushBlockedState(seq, reason) else null
        }
    val deviceId: String
        get() = pairingGate.read { readOrCreateDeviceId() }

    private fun readOrCreateDeviceId(): String {
        sp.getString("device_id", null)?.let { return it }
        val id = "android-" + UUID.randomUUID().toString().take(8)
        if (!sp.edit().putString("device_id", id).commit()) {
            throw IOException("sync device identity write failed")
        }
        return id
    }

    internal fun markSyncPushBlocked(state: SyncPushBlockedState) {
        require(state.seq > 0L) { "blocked sync sequence must be positive" }
        val stored = sp.edit()
            .putLong(PUSH_BLOCKED_SEQ, state.seq)
            .putString(PUSH_BLOCKED_REASON, state.reason.code)
            .commit()
        if (!stored) throw IOException("sync blocked state write failed")
    }

    internal fun clearSyncPushBlocked() {
        if (!sp.contains(PUSH_BLOCKED_SEQ) && !sp.contains(PUSH_BLOCKED_REASON)) return
        val cleared = sp.edit()
            .remove(PUSH_BLOCKED_SEQ)
            .remove(PUSH_BLOCKED_REASON)
            .commit()
        if (!cleared) throw IOException("sync blocked state clear failed")
    }

    /** Install a freshly redeemed token without carrying an old peer's blocked
     * acknowledgement state into the new pairing. Marker persistence must
     * succeed before the token is installed. */
    internal fun replaceToken(token: String) {
        pairingGate.replace {
            clearStoredToken()
            installFreshToken(token)
        }
    }

    internal fun replaceTokenIfCurrent(
        expected: SyncRequestSnapshot,
        token: String,
        serverDevice: String?,
    ): Boolean =
        pairingGate.replacePairingIfCurrent(
            expected = expected,
            endpointChanged = false,
            replace = {
                clearStoredToken()
                updateServerIdentityAndResetCursorIfChanged(serverDevice)
                installFreshToken(token)
            },
        )

    private fun installFreshToken(token: String) {
        clearSyncPushBlocked()
        tokenStore.set(token)
    }

    /** Commit a new host/token pairing without exposing a token to the wrong
     * host. The process-wide gate makes endpoint/token reads atomic across all
     * Settings instances. Separate stores are still written fail-closed:
     * clear token -> synchronously write endpoint -> write fresh token. */
    fun replacePairing(host: String, token: String) {
        pairingGate.replaceEndpoint {
            replacePairingStorage(host, sp.getInt("port", 8787), token)
        }
    }

    internal fun replacePairingIfCurrent(
        expected: SyncRequestSnapshot,
        host: String,
        token: String,
        serverDevice: String?,
    ): Boolean {
        val normalizedHost = normalizeSyncHostOrNull(host) ?: return false
        if (normalizedHost != expected.host) return false
        return pairingGate.replacePairingIfCurrent(
            expected = expected,
            endpointChanged = true,
            replace = { replacePairingStorage(normalizedHost, expected.port, token, serverDevice) },
        )
    }

    private fun replacePairingStorage(
        host: String,
        port: Int,
        token: String,
        serverDevice: String? = null,
    ) {
        clearStoredToken()
        val editor = sp.edit()
            .putString("host", host)
            .putInt("port", port)
            // An explicit endpoint pairing starts a new server history. The
            // current protocol has no durable database-generation ID, so even
            // the same host/server_device must replay from zero after a desktop
            // database rebuild; duplicate apply is safer than skipped events.
            .putLong("since", 0L)
            .remove(LEGACY_TOKEN)
        if (serverDevice != null) editor.putString(SERVER_DEVICE, serverDevice)
        else editor.remove(SERVER_DEVICE)
        val endpointStored = editor.commit()
        if (!endpointStored) {
            clearStoredToken()
            throw IOException("pairing state write failed")
        }
        // A blocked acknowledgement can belong to the previous peer. Clear the
        // safe marker before installing the fresh token so the new pairing gets
        // one re-evaluation; invalid/oversized local rows will block again.
        // clearSyncPushBlocked() is synchronous and throws before token install
        // if persistence fails, preserving the fail-closed ordering.
        installFreshToken(token)
    }

    private fun updateServerIdentityAndResetCursorIfChanged(serverDevice: String?) {
        if (serverDevice == null || sp.getString(SERVER_DEVICE, null) == serverDevice) return
        val stored = sp.edit()
            .putString(SERVER_DEVICE, serverDevice)
            .putLong("since", 0L)
            .commit()
        if (!stored) throw IOException("sync server identity write failed")
    }

    internal fun requestSnapshot(hostOverride: String?, auth: Boolean): SyncRequestSnapshot {
        val read = { revision: Long, endpointRevision: Long ->
            readRequestSnapshot(
                hostOverride = hostOverride,
                auth = auth,
                revision = revision,
                endpointRevision = endpointRevision,
                pairingAttempt = null,
            )
        }
        return if (auth) {
            pairingGate.authenticatedSnapshot(read)
        } else {
            pairingGate.snapshot(read)
        }
    }

    internal fun beginPairingSnapshot(hostOverride: String?): SyncRequestSnapshot =
        pairingGate.beginPairingSnapshot { revision, endpointRevision, pairingAttempt ->
            readRequestSnapshot(
                hostOverride = hostOverride,
                auth = false,
                revision = revision,
                endpointRevision = endpointRevision,
                pairingAttempt = pairingAttempt,
                pairingDeviceId = readOrCreateDeviceId(),
                outboxBaseSeq = ClipVaultApp.db(appCtx).outbox().pairingBaseSeq(),
            )
        }

    private fun readRequestSnapshot(
        hostOverride: String?,
        auth: Boolean,
        revision: Long,
        endpointRevision: Long,
        pairingAttempt: Long?,
        pairingDeviceId: String? = null,
        outboxBaseSeq: Long? = null,
    ): SyncRequestSnapshot {
        val storedHost = normalizeSyncHostOrNull(sp.getString("host", null))
        val host = normalizeSyncHostOrNull(hostOverride ?: storedHost)
            ?: throw IOException("invalid sync host")
        if (auth && hostOverride != null && host != storedHost) {
            throw IOException("authenticated sync host override rejected")
        }
        val bearerToken = if (auth) tokenStore.get() else null
        if (auth && bearerToken.isNullOrEmpty()) throw SyncAuthException()
        return SyncRequestSnapshot(
            host = host,
            port = sp.getInt("port", 8787),
            bearerToken = bearerToken,
            revision = revision,
            endpointRevision = endpointRevision,
            pairingAttempt = pairingAttempt,
            pairingDeviceId = pairingDeviceId,
            outboxBaseSeq = outboxBaseSeq,
        )
    }

    internal fun clearTokenIfCurrent(expected: SyncRequestSnapshot): Boolean =
        pairingGate.clearRejectedIfCurrent(
            expected = expected,
            currentStoreMatches = { currentEndpointMatches(expected) },
            clear = { clearStoredToken() },
        )

    internal fun isCurrent(expected: SyncRequestSnapshot): Boolean =
        pairingGate.isCurrent(expected) { currentEndpointMatches(expected) }

    internal fun runIfCurrent(expected: SyncRequestSnapshot, block: () -> Unit): Boolean =
        pairingGate.runIfCurrent(
            expected = expected,
            currentStoreMatches = { currentEndpointMatches(expected) },
            block = block,
        )

    /** Compare the response's source cursor with durable storage before any
     * page side effect. Apply and synchronous cursor persistence are linearized
     * with pairing replacement and every other Settings instance. */
    internal fun applyPullPageIfCurrent(
        expected: SyncRequestSnapshot,
        expectedSince: Long,
        nextSince: Long,
        applyPage: () -> Unit,
    ): Boolean = pairingGate.runIfCurrent(
        expected = expected,
        currentStoreMatches = {
            currentEndpointMatches(expected) && sp.getLong("since", 0L) == expectedSince
        },
        block = {
            applyPage()
            if (!sp.edit().putLong("since", nextSince).commit()) {
                throw IOException("sync cursor write failed")
            }
        },
    )

    internal fun tryBeginSyncFlight(): SyncFlightLease? = pairingGate.tryBeginSyncFlight()

    internal fun finishSyncFlight(lease: SyncFlightLease): Boolean =
        pairingGate.finishSyncFlight(lease)

    internal fun finishPairingIfCurrent(expected: SyncRequestSnapshot): Boolean =
        pairingGate.finishPairingIfCurrent(expected)

    private fun currentEndpointMatches(expected: SyncRequestSnapshot): Boolean =
        normalizeSyncHostOrNull(sp.getString("host", null)) == expected.host &&
            sp.getInt("port", 8787) == expected.port

    private fun clearStoredToken() {
        val cleared = sp.edit()
            .remove(LEGACY_TOKEN)
            .putBoolean(LEGACY_TOKEN_MIGRATED, true)
            .commit()
        if (!cleared) throw IOException("legacy token cleanup failed")
        // Only clear the secure token after the old plaintext location is
        // durably retired. A crash before this line leaves the unchanged old
        // host/token pairing; a crash after it can only leave no token.
        tokenStore.set(null)
    }

    /** One-time v1.2.x -> v1.3 migration. Preserve pairing while deleting the
     * old plaintext token from the legacy sync preference file. */
    private fun migrateLegacyToken() {
        if (sp.getBoolean(LEGACY_TOKEN_MIGRATED, false)) {
            if (sp.contains(LEGACY_TOKEN) && !sp.edit().remove(LEGACY_TOKEN).commit()) {
                throw IOException("legacy token cleanup failed")
            }
            return
        }
        val legacy = sp.getString(LEGACY_TOKEN, null)
        if (!legacy.isNullOrEmpty() && tokenStore.get().isNullOrEmpty()) {
            tokenStore.set(legacy)
        }
        val migrated = sp.edit()
            .remove(LEGACY_TOKEN)
            .putBoolean(LEGACY_TOKEN_MIGRATED, true)
            .commit()
        if (!migrated) throw IOException("token migration state write failed")
    }
}

/** Minimal HTTP sync client (SYNC-2). Uses HttpURLConnection — no extra deps. */
class SyncClient private constructor(
    private val s: Settings,
    private val hostOverride: String?,
    private val fixedSnapshot: SyncRequestSnapshot?,
) {
    constructor(s: Settings, hostOverride: String? = null) : this(s, hostOverride, null)

    internal constructor(s: Settings, snapshot: SyncRequestSnapshot) : this(s, null, snapshot)

    private class SyncHttpResponse(
        val code: Int,
        val text: String,
        val request: SyncRequestSnapshot,
    )

    private class PairingRedemption(
        val token: String,
        val serverDevice: String?,
        val request: SyncRequestSnapshot,
    )

    private fun req(
        method: String,
        path: String,
        body: String?,
        auth: Boolean,
        requestOverride: SyncRequestSnapshot? = null,
    ): SyncHttpResponse {
        val bodyBytes = body?.toByteArray(Charsets.UTF_8)
        val snapshot = requestOverride ?: fixedSnapshot ?: s.requestSnapshot(hostOverride, auth)
        if (auth && snapshot.bearerToken.isNullOrEmpty()) throw SyncAuthException()
        if (fixedSnapshot != null && !s.isCurrent(snapshot)) throw SyncPairingChangedException()
        val c = (URL(snapshot.baseUrl + path).openConnection() as HttpURLConnection).apply {
            // ClipVault sync endpoints never redirect. Keep bearer tokens scoped
            // to the paired host instead of following a 3xx to another URL.
            instanceFollowRedirects = false
            requestMethod = method
            connectTimeout = 8000; readTimeout = 12000
            if (auth) snapshot.bearerToken?.let { setRequestProperty("Authorization", "Bearer $it") }
            if (bodyBytes != null) {
                doOutput = true
                setRequestProperty("Content-Type", "application/json")
                setFixedLengthStreamingMode(bodyBytes.size)
            }
        }
        try {
            if (bodyBytes != null) c.outputStream.use { it.write(bodyBytes) }
            val code = c.responseCode
            if (auth && isPermanentSyncAuthFailure(code)) {
                if (!s.clearTokenIfCurrent(snapshot)) throw SyncPairingChangedException()
            } else if (auth && !s.isCurrent(snapshot)) {
                throw SyncPairingChangedException()
            }
            val stream = if (code in 200..299) c.inputStream else c.errorStream
            // For authenticated sync, 401/403 already tell us the local bearer
            // token is invalid for this paired desktop. Do not let an oversized
            // error body hide that permanent auth signal behind a generic IO
            // retry; callers do not need the body for that state.
            val text = if (shouldReadSyncResponseBody(code, auth)) {
                stream?.use { readUtf8BodyBounded(it) } ?: ""
            } else {
                ""
            }
            return SyncHttpResponse(code, text, snapshot)
        } finally {
            c.disconnect()
        }
    }

    /** Redeem a one-time pairing code shown on the desktop Web UI.
     * Never throws: any network/parse failure returns false so the UI shows a
     * "配对失败" message instead of crashing the app. */
    fun pair(code: String): Boolean {
        return try {
            val redemption = requestPairToken(code) ?: return false
            try {
                s.replaceTokenIfCurrent(
                    redemption.request,
                    redemption.token,
                    redemption.serverDevice,
                )
            } finally {
                s.finishPairingIfCurrent(redemption.request)
            }
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
            val redemption = SyncClient(s, h).requestPairToken(code) ?: return false
            try {
                s.replacePairingIfCurrent(
                    redemption.request,
                    h,
                    redemption.token,
                    redemption.serverDevice,
                )
            } finally {
                s.finishPairingIfCurrent(redemption.request)
            }
        } catch (e: Exception) {
            android.util.Log.w("clipvault.sync", "pair failed: ${e.javaClass.simpleName}")
            false
        }
    }

    private fun requestPairToken(code: String): PairingRedemption? {
        val pairingSnapshot = s.beginPairingSnapshot(hostOverride)
        var handedOff = false
        try {
            val deviceId = pairingSnapshot.pairingDeviceId ?: return null
            val outboxBaseSeq = pairingSnapshot.outboxBaseSeq ?: return null
            val body = JSONObject()
                .put("code", code)
                .put("device_id", deviceId)
                .put("device_name", android.os.Build.MODEL ?: "Android")
                .put("outbox_base_seq", outboxBaseSeq)
                .toString()
            val response = req(
                "POST",
                "/pair",
                body,
                auth = false,
                requestOverride = pairingSnapshot,
            )
            if (response.code != 200) return null
            val parsed = parsePairingResponse(response.text, outboxBaseSeq) ?: return null
            handedOff = true
            return PairingRedemption(parsed.token, parsed.serverDevice, response.request)
        } finally {
            if (!handedOff) s.finishPairingIfCurrent(pairingSnapshot)
        }
    }

    fun push(events: JSONArray): Long {
        val body = JSONObject().put("events", events).toString()
        val response = req("POST", "/sync/push", body, auth = true)
        val code = response.code
        val text = response.text
        if (code == 200) return JSONObject(text).optLong("acked_upto", 0)
        if (isPermanentSyncAuthFailure(code)) throw SyncAuthException()
        // A paired older desktop can still enforce the former 4 MiB cap. This
        // request cannot succeed unchanged, so let the worker persist a safe
        // blocked marker instead of retrying it every period.
        if (code == 413) throw SyncPushRequestTooLargeException()
        return -1
    }

    fun pull(since: Long): JSONObject? {
        val response = req("GET", "/sync/pull?since_seq=$since", null, auth = true)
        val code = response.code
        val text = response.text
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
                    "privacy_noop" -> {
                        val payload = ev.getJSONObject("payload")
                        if (payload.length() != 0 ||
                            ev.getString("created_at") != PRIVACY_NOOP_TIMESTAMP
                        ) {
                            throw org.json.JSONException("invalid privacy noop")
                        }
                    }
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
        val patch = d.getJSONObject("patch")
        val parsed = parseClipMetaPatch(patch)
        if (parsed.isEmpty) return
        db.clips().applyMetaPatch(
            hash = hash,
            pinned = parsed.pinned,
            favorite = parsed.favorite,
            deleted = parsed.deleted,
        )
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
