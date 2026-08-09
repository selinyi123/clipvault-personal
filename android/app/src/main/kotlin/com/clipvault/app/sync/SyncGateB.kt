package com.clipvault.app.sync

import com.clipvault.app.data.OutboxEntity
import com.clipvault.core.CONTENT_TYPES
import com.clipvault.core.Normalize
import com.clipvault.core.SECRET_LEVEL_HARD
import com.clipvault.core.SECRET_LEVEL_SUSPECT
import com.clipvault.core.SecretGuard
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import java.time.Instant
import java.time.format.DateTimeParseException

internal const val PRIVACY_NOOP_KIND = "privacy_noop"
internal const val PRIVACY_NOOP_TIMESTAMP = "1970-01-01T00:00:00Z"

private val CLIP_NEW_FIELDS = setOf(
    "id",
    "content",
    "content_hash",
    "content_type",
    "is_secret",
    "secret_level",
    "secret_reasons",
    "source_device",
    "source_app",
    "created_at",
    "last_seen_at",
    "times_seen",
    "pinned",
    "favorite",
    "deleted",
)
private val PULL_EVENT_FIELDS = setOf("seq", "kind", "payload", "created_at")
private val CLIP_META_FIELDS = setOf("content_hash", "patch", "ts")
private val CLIP_META_PATCH_FIELDS = setOf("pinned", "favorite", "deleted")
private val MEMORY_UPSERT_FIELDS = setOf("kind", "text", "label", "pinned", "use_count", "source")
private val MEMORY_DELETE_FIELDS = setOf("kind", "text", "ts")
private val KNOWN_PULL_EVENT_KINDS = setOf(
    "clip_new",
    "clip_meta",
    "memory_upsert",
    "memory_delete",
    PRIVACY_NOOP_KIND,
)
private val CLIP_ID = Regex("^[0-9A-Za-z]{1,128}$")
private val ULID = Regex("^[0-7][0-9A-HJKMNP-TV-Z]{25}$")
private val CONTENT_HASH = Regex("^[0-9a-f]{64}$")
private val UTC_SECONDS = Regex("^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z$")
private val MEMORY_KINDS = setOf("term", "phrase", "prompt", "command", "key_info", "path")
private val MEMORY_SOURCES = setOf("manual", "derived", "obsidian_import", "github_import")
private const val MAX_MEMORY_LABEL_BYTES = 4 * 1024
private const val MAX_JSON_NESTING_DEPTH = 32

// Deliberately not a data class: the generated toString() would expose public
// clip content if a future caller logged a projection while debugging sync.
internal class ProjectedSyncEvent(
    val kind: String,
    val timestamp: String,
    val data: JSONObject,
)

/** Validate one desktop pull event before any field can reach Room.
 *
 * Known event kinds use the same payload/type/normalization constraints as the
 * desktop apply boundary. Unknown kinds remain a forward-compatible no-op. A
 * seq-valid malformed known event throws JSONException so SyncApply can skip it
 * and let the caller durably advance the pull cursor.
 */
internal fun validatePulledEvent(event: JSONObject): String? {
    val kind = event.opt("kind") as? String
        ?: throw JSONException("event kind must be a String")
    if (kind !in KNOWN_PULL_EVENT_KINDS) return null
    if (jsonKeys(event) != PULL_EVENT_FIELDS) throw JSONException("invalid event fields")
    if (strictPositiveLong(event.opt("seq")) == null) throw JSONException("invalid event seq")

    val createdAt = strictUtcInstant(event.opt("created_at"))
        ?: throw JSONException("invalid event timestamp")
    val payload = event.opt("payload") as? JSONObject
        ?: throw JSONException("event payload must be an object")

    when (kind) {
        "clip_new" -> validatePulledClipNew(payload)
        "clip_meta" -> validatePulledClipMeta(payload)
        "memory_upsert" -> validatePulledMemoryUpsert(payload)
        "memory_delete" -> validatePulledMemoryDelete(payload)
        PRIVACY_NOOP_KIND -> {
            if (payload.length() != 0 || createdAt.toString() != PRIVACY_NOOP_TIMESTAMP) {
                throw JSONException("invalid privacy noop")
            }
        }
    }
    return kind
}

private fun validatePulledClipNew(payload: JSONObject) {
    if (jsonKeys(payload) != CLIP_NEW_FIELDS) throw JSONException("invalid clip fields")

    val id = payload.opt("id") as? String ?: throw JSONException("invalid clip id")
    if (!CLIP_ID.matches(id)) throw JSONException("invalid clip id")

    val content = payload.opt("content") as? String ?: throw JSONException("invalid clip content")
    val normalized = Normalize.normalize(content)
    if (normalized != content || Normalize.rejectReason(normalized) != null) {
        throw JSONException("invalid normalized clip content")
    }

    val contentHash = payload.opt("content_hash") as? String
        ?: throw JSONException("invalid clip hash")
    if (!CONTENT_HASH.matches(contentHash) || Normalize.contentHash(content) != contentHash) {
        throw JSONException("invalid clip hash")
    }
    val contentType = payload.opt("content_type") as? String
        ?: throw JSONException("invalid clip content type")
    if (contentType !in CONTENT_TYPES) throw JSONException("invalid clip content type")

    val declaredSecret = payload.opt("is_secret")
    if (declaredSecret !is Boolean) throw JSONException("invalid clip secret flag")
    val secretLevel = payload.opt("secret_level")
    val secretReasons = payload.opt("secret_reasons")
    if (declaredSecret) {
        if (secretLevel !is String || secretLevel !in setOf(SECRET_LEVEL_HARD, SECRET_LEVEL_SUSPECT)) {
            throw JSONException("invalid clip secret level")
        }
        if (!validSecretReasons(secretReasons)) throw JSONException("invalid clip secret reasons")
    } else if (secretLevel !== JSONObject.NULL || secretReasons !is JSONArray || secretReasons.length() != 0) {
        throw JSONException("invalid public clip secret metadata")
    }

    val sourceDevice = payload.opt("source_device") as? String
        ?: throw JSONException("invalid clip source device")
    if (
        sourceDevice.isEmpty() ||
        sourceDevice.length > 256 ||
        hasControlChars(sourceDevice) ||
        SecretGuard.scan(sourceDevice).isSecret
    ) throw JSONException("invalid clip source device")
    val sourceApp = payload.opt("source_app")
    if (
        sourceApp !== JSONObject.NULL &&
        (sourceApp !is String ||
            sourceApp.length > 1024 ||
            hasControlChars(sourceApp) ||
            SecretGuard.scan(sourceApp).isSecret)
    ) throw JSONException("invalid clip source app")

    val createdAt = strictUtcInstant(payload.opt("created_at"))
        ?: throw JSONException("invalid clip created timestamp")
    val lastSeenAt = strictUtcInstant(payload.opt("last_seen_at"))
        ?: throw JSONException("invalid clip last-seen timestamp")
    if (lastSeenAt.isBefore(createdAt)) throw JSONException("invalid clip timestamp order")

    val timesSeen = strictPositiveLong(payload.opt("times_seen"))
        ?: throw JSONException("invalid clip times seen")
    if (timesSeen > Int.MAX_VALUE.toLong()) throw JSONException("invalid clip times seen")
    for (field in listOf("pinned", "favorite", "deleted")) {
        if (payload.opt(field) !is Boolean) throw JSONException("invalid clip Boolean")
    }
}

private fun validatePulledClipMeta(payload: JSONObject) {
    if (jsonKeys(payload) != CLIP_META_FIELDS) throw JSONException("invalid clip metadata fields")
    val contentHash = payload.opt("content_hash") as? String
        ?: throw JSONException("invalid clip metadata hash")
    if (!CONTENT_HASH.matches(contentHash)) throw JSONException("invalid clip metadata hash")
    if (strictUtcInstant(payload.opt("ts")) == null) throw JSONException("invalid clip metadata timestamp")
    val patch = payload.opt("patch") as? JSONObject
        ?: throw JSONException("invalid clip metadata patch")
    val patchFields = jsonKeys(patch)
    if (patchFields.isEmpty() || !CLIP_META_PATCH_FIELDS.containsAll(patchFields)) {
        throw JSONException("invalid clip metadata patch")
    }
    if (patchFields.any { patch.opt(it) !is Boolean }) {
        throw JSONException("invalid clip metadata value")
    }
}

private fun validatePulledMemoryUpsert(payload: JSONObject) {
    val fields = jsonKeys(payload)
    if (!MEMORY_UPSERT_FIELDS.containsAll(fields)) throw JSONException("invalid memory fields")
    validateMemoryKey(payload)

    if (payload.has("label")) {
        val label = payload.opt("label")
        if (label !== JSONObject.NULL && (label !is String || utf8Size(label) > MAX_MEMORY_LABEL_BYTES)) {
            throw JSONException("invalid memory label")
        }
    }
    if (payload.has("pinned") && payload.opt("pinned") !is Boolean) {
        throw JSONException("invalid memory pinned flag")
    }
    if (payload.has("use_count")) {
        val useCount = strictNonNegativeLong(payload.opt("use_count"))
            ?: throw JSONException("invalid memory use count")
        if (useCount > Int.MAX_VALUE.toLong()) throw JSONException("invalid memory use count")
    }
    if (payload.has("source")) {
        val source = payload.opt("source") as? String
            ?: throw JSONException("invalid memory source")
        if (source !in MEMORY_SOURCES || hasControlChars(source)) {
            throw JSONException("invalid memory source")
        }
    }
}

private fun validatePulledMemoryDelete(payload: JSONObject) {
    if (jsonKeys(payload) != MEMORY_DELETE_FIELDS) throw JSONException("invalid memory delete fields")
    validateMemoryKey(payload)
    if (strictUtcInstant(payload.opt("ts")) == null) throw JSONException("invalid memory timestamp")
}

private fun validateMemoryKey(payload: JSONObject) {
    val kind = payload.opt("kind") as? String ?: throw JSONException("invalid memory kind")
    if (kind !in MEMORY_KINDS) throw JSONException("invalid memory kind")
    val text = payload.opt("text") as? String ?: throw JSONException("invalid memory text")
    if (text.isBlank() || text != text.trim() || utf8Size(text) > Normalize.DEFAULT_MAX_CLIP_BYTES) {
        throw JSONException("invalid memory text")
    }
}

private fun strictPositiveLong(value: Any?): Long? = when (value) {
    is Int -> value.toLong().takeIf { it > 0L }
    is Long -> value.takeIf { it > 0L }
    else -> null
}

private fun strictNonNegativeLong(value: Any?): Long? = when (value) {
    is Int -> value.toLong().takeIf { it >= 0L }
    is Long -> value.takeIf { it >= 0L }
    else -> null
}

private fun utf8Size(value: String): Int = value.toByteArray(Charsets.UTF_8).size

/** Parse only RFC 8259 JSON objects before handing the text to Android's
 * deliberately lenient org.json implementation. The validator rejects
 * trailing input, extensions such as single-quoted strings/unquoted names,
 * and duplicate object keys (including keys written with unicode escapes).
 */
internal fun parseStrictJsonObject(raw: String): JSONObject? {
    if (!StrictJsonValidator(raw).validateRootObject()) return null
    return try {
        JSONObject(raw)
    } catch (_: JSONException) {
        null
    }
}

private class StrictJsonValidator(private val raw: String) {
    private var index = 0

    fun validateRootObject(): Boolean {
        skipWhitespace()
        if (!parseObject(depth = 0)) return false
        skipWhitespace()
        return index == raw.length
    }

    private fun parseValue(depth: Int): Boolean {
        skipWhitespace()
        if (index >= raw.length) return false
        return when (raw[index]) {
            '{' -> parseObject(depth)
            '[' -> parseArray(depth)
            '"' -> parseString(captureDecoded = false) != null
            't' -> consumeLiteral("true")
            'f' -> consumeLiteral("false")
            'n' -> consumeLiteral("null")
            '-', in '0'..'9' -> parseNumber()
            else -> false
        }
    }

    private fun parseObject(depth: Int): Boolean {
        if (depth > MAX_JSON_NESTING_DEPTH || !consume('{')) return false
        skipWhitespace()
        if (consume('}')) return true

        val keys = mutableSetOf<String>()
        while (true) {
            skipWhitespace()
            val key = parseString(captureDecoded = true) ?: return false
            if (!keys.add(key)) return false
            skipWhitespace()
            if (!consume(':') || !parseValue(depth + 1)) return false
            skipWhitespace()
            if (consume('}')) return true
            if (!consume(',')) return false
        }
    }

    private fun parseArray(depth: Int): Boolean {
        if (depth > MAX_JSON_NESTING_DEPTH || !consume('[')) return false
        skipWhitespace()
        if (consume(']')) return true
        while (true) {
            if (!parseValue(depth + 1)) return false
            skipWhitespace()
            if (consume(']')) return true
            if (!consume(',')) return false
        }
    }

    private fun parseString(captureDecoded: Boolean): String? {
        if (!consume('"')) return null
        // Values can contain the full 1 MiB clip. Decode only object keys,
        // where the decoded form is needed to detect escaped duplicates.
        val decoded = if (captureDecoded) StringBuilder() else null
        while (index < raw.length) {
            val ch = raw[index++]
            when {
                ch == '"' -> return decoded?.toString() ?: ""
                ch.code < 0x20 -> return null
                ch != '\\' -> decoded?.append(ch)
                index >= raw.length -> return null
                else -> {
                    when (val escaped = raw[index++]) {
                        '"', '\\', '/' -> decoded?.append(escaped)
                        'b' -> decoded?.append('\b')
                        'f' -> decoded?.append('\u000c')
                        'n' -> decoded?.append('\n')
                        'r' -> decoded?.append('\r')
                        't' -> decoded?.append('\t')
                        'u' -> {
                            if (index + 4 > raw.length) return null
                            var code = 0
                            repeat(4) {
                                val digit = hexValue(raw[index + it])
                                if (digit < 0) return null
                                code = (code shl 4) or digit
                            }
                            decoded?.append(code.toChar())
                            index += 4
                        }
                        else -> return null
                    }
                }
            }
        }
        return null
    }

    private fun parseNumber(): Boolean {
        if (consume('-') && index >= raw.length) return false
        if (consume('0')) {
            if (index < raw.length && raw[index] in '0'..'9') return false
        } else {
            if (index >= raw.length || raw[index] !in '1'..'9') return false
            while (index < raw.length && raw[index] in '0'..'9') index += 1
        }

        if (consume('.')) {
            if (index >= raw.length || raw[index] !in '0'..'9') return false
            while (index < raw.length && raw[index] in '0'..'9') index += 1
        }
        if (index < raw.length && raw[index] in charArrayOf('e', 'E')) {
            index += 1
            if (index < raw.length && raw[index] in charArrayOf('+', '-')) index += 1
            if (index >= raw.length || raw[index] !in '0'..'9') return false
            while (index < raw.length && raw[index] in '0'..'9') index += 1
        }
        return true
    }

    private fun consumeLiteral(value: String): Boolean {
        if (!raw.regionMatches(index, value, 0, value.length)) return false
        index += value.length
        return true
    }

    private fun hexValue(value: Char): Int = when (value) {
        in '0'..'9' -> value - '0'
        in 'a'..'f' -> value - 'a' + 10
        in 'A'..'F' -> value - 'A' + 10
        else -> -1
    }

    private fun consume(expected: Char): Boolean {
        if (index >= raw.length || raw[index] != expected) return false
        index += 1
        return true
    }

    private fun skipWhitespace() {
        while (index < raw.length && raw[index] in charArrayOf(' ', '\t', '\r', '\n')) index += 1
    }
}

/** Revalidate a durable row at Secret Guard Gate B before any wire encoding.
 *
 * A legacy clip that is secret under the current rules becomes a content-free
 * same-sequence no-op. Structural corruption remains blocked: guessing at a
 * malformed or future event kind could silently retire data that a newer app
 * knows how to repair.
 */
internal fun projectOutboxRowForWire(
    row: OutboxEntity,
    payload: JSONObject,
): ProjectedSyncEvent? {
    if (row.seq <= 0L || row.kind != "clip_new") return null
    if (jsonKeys(payload) != CLIP_NEW_FIELDS) return null

    val declaredSecret = payload.opt("is_secret")
    if (declaredSecret !is Boolean) return null

    val content = payload.opt("content")
    if (content !is String) return null
    val normalized = Normalize.normalize(content)
    if (Normalize.rejectReason(normalized) != null) return null
    if (normalized != content) return null

    val id = payload.opt("id")
    val contentHash = payload.opt("content_hash")
    val contentType = payload.opt("content_type")
    if (id !is String || !ULID.matches(id)) return null
    if (
        contentHash !is String ||
        !CONTENT_HASH.matches(contentHash) ||
        Normalize.contentHash(content) != contentHash
    ) return null
    if (contentType !is String || contentType !in CONTENT_TYPES) return null
    val secretLevel = payload.opt("secret_level")
    val secretReasons = payload.opt("secret_reasons")
    if (declaredSecret) {
        if (secretLevel !is String || secretLevel !in setOf(SECRET_LEVEL_HARD, SECRET_LEVEL_SUSPECT)) {
            return null
        }
        if (!validSecretReasons(secretReasons)) return null
    } else {
        if (secretLevel !== JSONObject.NULL) return null
        if (secretReasons !is JSONArray || secretReasons.length() != 0) return null
    }

    val sourceDevice = payload.opt("source_device")
    if (
        sourceDevice !is String ||
        sourceDevice.isEmpty() ||
        sourceDevice.length > 256 ||
        hasControlChars(sourceDevice)
    ) return null
    val sourceApp = payload.opt("source_app")
    if (
        sourceApp !== JSONObject.NULL &&
        (sourceApp !is String || sourceApp.length > 1024 || hasControlChars(sourceApp))
    ) return null

    val createdAt = strictUtcInstant(payload.opt("created_at")) ?: return null
    val lastSeenAt = strictUtcInstant(payload.opt("last_seen_at")) ?: return null
    if (lastSeenAt.isBefore(createdAt)) return null
    if (strictUtcInstant(row.createdAt) == null) return null

    val timesSeen = when (val value = payload.opt("times_seen")) {
        is Int -> value.toLong()
        is Long -> value
        else -> return null
    }
    if (timesSeen !in 1L..Int.MAX_VALUE.toLong()) return null
    for (field in listOf("pinned", "favorite", "deleted")) {
        if (payload.opt(field) !is Boolean) return null
    }

    if (declaredSecret || SecretGuard.scan(normalized).isSecret) return privacyNoop()
    return ProjectedSyncEvent("clip_new", row.createdAt, payload)
}

private fun privacyNoop(): ProjectedSyncEvent = ProjectedSyncEvent(
    kind = PRIVACY_NOOP_KIND,
    timestamp = PRIVACY_NOOP_TIMESTAMP,
    data = JSONObject(),
)

internal fun jsonKeys(value: JSONObject): Set<String> {
    val keys = mutableSetOf<String>()
    val iterator = value.keys()
    while (iterator.hasNext()) keys += iterator.next()
    return keys
}

private fun strictUtcInstant(value: Any?): Instant? {
    if (value !is String || !UTC_SECONDS.matches(value)) return null
    if (value.startsWith("0000-")) return null
    return try {
        Instant.parse(value).takeIf { it.toString() == value }
    } catch (_: DateTimeParseException) {
        null
    }
}

private fun validSecretReasons(value: Any?): Boolean {
    if (value !is JSONArray || value.length() == 0) return false
    for (index in 0 until value.length()) {
        val reason = value.opt(index)
        if (reason !is String || reason.isEmpty() || hasControlChars(reason)) return false
    }
    return true
}

private fun hasControlChars(value: String): Boolean =
    value.any { it.code < 32 || it.code == 127 }
