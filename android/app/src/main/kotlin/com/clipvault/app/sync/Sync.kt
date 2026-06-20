package com.clipvault.app.sync

import android.content.Context
import com.clipvault.app.ClipVaultApp
import com.clipvault.app.data.AppDatabase
import com.clipvault.app.data.ClipEntity
import com.clipvault.core.SecretGuard
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID

/** Device-local sync settings (token lives here; on a real build move it to
 * EncryptedSharedPreferences / Keystore). */
class Settings(context: Context) {
    private val sp = context.getSharedPreferences("clipvault_sync", Context.MODE_PRIVATE)
    var host: String?  get() = sp.getString("host", null);  set(v) { sp.edit().putString("host", v).apply() }
    var port: Int      get() = sp.getInt("port", 8787);     set(v) { sp.edit().putInt("port", v).apply() }
    var token: String? get() = sp.getString("token", null); set(v) { sp.edit().putString("token", v).apply() }
    var sinceSeq: Long get() = sp.getLong("since", 0);      set(v) { sp.edit().putLong("since", v).apply() }
    val deviceId: String
        get() = sp.getString("device_id", null) ?: run {
            val id = "android-" + UUID.randomUUID().toString().take(8)
            sp.edit().putString("device_id", id).apply(); id
        }
}

/** Minimal HTTP sync client (SYNC-2). Uses HttpURLConnection — no extra deps. */
class SyncClient(private val s: Settings) {
    private fun base() = "http://${s.host}:${s.port}/api"

    private fun req(method: String, path: String, body: String?, auth: Boolean): Pair<Int, String> {
        val c = (URL(base() + path).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 8000; readTimeout = 12000
            if (auth) s.token?.let { setRequestProperty("Authorization", "Bearer $it") }
            if (body != null) {
                doOutput = true
                setRequestProperty("Content-Type", "application/json")
                outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            }
        }
        val code = c.responseCode
        val stream = if (code in 200..299) c.inputStream else (c.errorStream ?: c.inputStream)
        val text = stream.bufferedReader().use(BufferedReader::readText)
        c.disconnect()
        return code to text
    }

    /** Redeem a one-time pairing code shown on the desktop Web UI.
     * Never throws: any network/parse failure returns false so the UI shows a
     * "配对失败" message instead of crashing the app. */
    fun pair(code: String): Boolean {
        return try {
            val body = JSONObject().put("code", code).put("device_id", s.deviceId)
                .put("device_name", android.os.Build.MODEL ?: "Android").toString()
            val (code2, text) = req("POST", "/pair", body, auth = false)
            if (code2 != 200) return false
            val token = JSONObject(text).optString("token", "")
            if (token.isEmpty()) return false
            s.token = token
            true
        } catch (e: Exception) {
            android.util.Log.w("clipvault.sync", "pair failed: ${e.javaClass.simpleName}")
            false
        }
    }

    fun push(events: JSONArray): Long {
        val body = JSONObject().put("events", events).toString()
        val (code, text) = req("POST", "/sync/push", body, auth = true)
        return if (code == 200) JSONObject(text).optLong("acked_upto", 0) else -1
    }

    fun pull(since: Long): JSONObject? {
        val (code, text) = req("GET", "/sync/pull?since_seq=$since", null, auth = true)
        return if (code == 200) JSONObject(text) else null
    }
}

/** Apply events pulled from the desktop into the local cache. Does NOT touch
 * the outbox (no echo). Gate A re-scan on arrival. */
object SyncApply {
    fun applyEvents(db: AppDatabase, events: JSONArray) {
        for (i in 0 until events.length()) {
            val ev = events.getJSONObject(i)
            when (ev.getString("kind")) {
                "clip_new" -> applyClipNew(db, ev.getJSONObject("payload"))
                "clip_meta" -> applyClipMeta(db, ev.getJSONObject("payload"))
                "memory_upsert" -> applyMemoryUpsert(db, ev.getJSONObject("payload"))
                "memory_delete" -> applyMemoryDelete(db, ev.getJSONObject("payload"))
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
        val clip = db.clips().byHash(d.getString("content_hash")) ?: return
        val patch = d.getJSONObject("patch")
        if (patch.optBoolean("deleted", false)) db.clips().softDelete(clip.id)
        // pin/favorite mirroring omitted in the cache UI for brevity; delete is the
        // user-visible one. Full field LWW lives on the desktop (source of truth).
    }

    private fun applyMemoryUpsert(db: AppDatabase, d: JSONObject) {
        db.memory().upsert(
            com.clipvault.app.data.MemoryEntity(
                kind = d.getString("kind"), text = d.getString("text"),
                label = if (d.isNull("label")) null else d.optString("label"),
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
