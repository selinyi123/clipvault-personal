package com.clipvault.app.capture

import com.clipvault.app.data.AppDatabase
import com.clipvault.app.data.ClipEntity
import com.clipvault.app.data.OutboxEntity
import com.clipvault.core.Classifier
import com.clipvault.core.Normalize
import com.clipvault.core.SecretGuard
import org.json.JSONArray
import org.json.JSONObject
import java.math.BigInteger
import java.security.SecureRandom
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone

/**
 * The single capture path on Android (Share Target / manual save / QS Tile /
 * IME-save all funnel here). Mirrors the desktop ingest pipeline order:
 * normalize -> reject -> dedup -> Secret Guard (gate A) -> classify -> store
 * -> outbox. Gate B: secrets never enter the outbox. No background polling.
 */
object Capture {
    enum class Status { NEW, DUPLICATE, REJECTED }
    data class Result(val status: Status, val clip: ClipEntity?) {
        /** True when the capture changed or found local state. */
        val didStoreLocally: Boolean
            get() = status != Status.REJECTED

        /** True only when ingest produced a public outbox event worth pushing. */
        val shouldRequestSyncPush: Boolean
            get() = status == Status.NEW && clip?.isSecret == false
    }

    private const val ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    private val rng = SecureRandom()
    private val mask31 = BigInteger.valueOf(31L)

    fun ingest(db: AppDatabase, raw: String, sourceDevice: String, sourceApp: String? = null): Result {
        val content = Normalize.normalize(raw)
        if (Normalize.rejectReason(content) != null) return Result(Status.REJECTED, null)

        val hash = Normalize.contentHash(content)
        val now = utcNow()

        // Preserve the cheap duplicate path without holding a writer transaction
        // while Secret Guard and classification run. Hard deletion is not part of
        // ClipDao's contract, so a row found here must still exist when rechecked.
        if (db.clips().byHash(hash) != null) {
            return db.runInTransaction<Result> {
                val existing = checkNotNull(db.clips().byHash(hash)) {
                    "clip disappeared during duplicate capture"
                }
                touchDuplicate(db, existing, now)
            }
        }

        // Deterministic planning stays outside the SQLite writer transaction.
        val verdict = SecretGuard.scan(content)          // gate A
        val type = Classifier.classify(content)
        val clip = ClipEntity(
            id = ulid(), content = content, contentHash = hash, contentType = type,
            isSecret = verdict.isSecret, secretLevel = verdict.level,
            secretReasons = JSONArray(verdict.reasons).toString(),
            sourceDevice = sourceDevice, sourceApp = sourceApp,
            createdAt = now, lastSeenAt = now, timesSeen = 1,
        )
        val plannedOutbox = if (clip.isSecret) {
            null
        } else {
            OutboxEntity(kind = "clip_new", payload = clipJson(clip), createdAt = now)
        }

        return db.runInTransaction<Result> {
            // Recheck under the writer transaction: another capture may have won
            // after the optimistic lookup and before this transaction began.
            val existing = db.clips().byHash(hash)
            if (existing != null) {
                return@runInTransaction touchDuplicate(db, existing, now)
            }

            val insertResult = db.clips().insert(clip)
            if (insertResult == -1L) {
                // INSERT IGNORE can lose either the content-hash race or the
                // (extremely unlikely) primary-key race. Only the former is a
                // valid duplicate; never emit an outbox row for the candidate ID.
                val winner = checkNotNull(db.clips().byHash(hash)) {
                    "clip insert was ignored without a matching content hash"
                }
                return@runInTransaction touchDuplicate(db, winner, now)
            }

            // Gate B: only public clips are published to the desktop. Room rolls
            // the clip insert back if appending this outbox row fails.
            plannedOutbox?.let(db.outbox()::append)
            Result(Status.NEW, clip)
        }
    }

    private fun touchDuplicate(db: AppDatabase, existing: ClipEntity, now: String): Result {
        db.clips().touchSeen(existing.id, now)
        val touched = checkNotNull(db.clips().byHash(existing.contentHash)) {
            "duplicate clip disappeared after touch"
        }
        return Result(Status.DUPLICATE, touched)
    }

    private fun clipJson(c: ClipEntity): String = JSONObject().apply {
        put("id", c.id); put("content", c.content); put("content_hash", c.contentHash)
        put("content_type", c.contentType); put("is_secret", c.isSecret)
        put("secret_level", c.secretLevel ?: JSONObject.NULL)
        put("secret_reasons", JSONArray(c.secretReasons))
        put("source_device", c.sourceDevice); put("source_app", c.sourceApp ?: JSONObject.NULL)
        put("created_at", c.createdAt); put("last_seen_at", c.lastSeenAt)
        put("times_seen", c.timesSeen); put("pinned", c.pinned)
        put("favorite", c.favorite); put("deleted", c.deleted)
    }.toString()

    private fun utcNow(): String {
        val fmt = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US)
        fmt.timeZone = TimeZone.getTimeZone("UTC")
        return fmt.format(java.util.Date())
    }

    /** DB-1 compatible ULID: 48-bit millisecond time + 80-bit randomness,
     * encoded as 26 Crockford Base32 characters. */
    private fun ulid(): String {
        val out = CharArray(26)
        var t = System.currentTimeMillis()
        for (i in 9 downTo 0) {
            out[i] = ULID_ALPHABET[(t and 31L).toInt()]
            t = t ushr 5
        }

        val random = ByteArray(10)
        rng.nextBytes(random)
        var r = BigInteger(1, random)
        for (i in 25 downTo 10) {
            out[i] = ULID_ALPHABET[r.and(mask31).toInt()]
            r = r.shiftRight(5)
        }
        return String(out)
    }
}
