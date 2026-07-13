package com.clipvault.app.sync

import android.content.Context
import android.util.Log
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import com.clipvault.app.ClipVaultApp
import com.clipvault.app.data.AppDatabase
import com.clipvault.app.data.OutboxEntity
import com.clipvault.app.data.OutboxMetadata
import com.clipvault.core.Normalize
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

// JSON requires U+0000..U+001F to be escaped. In the worst case, every byte of
// a valid 1 MiB clip becomes a six-byte backslash-u escape on the wire. Keep a
// bounded allowance for the fixed clip/event fields while staying below the
// desktop's 7 MiB sync-push hard cap.
private const val MAX_JSON_ESCAPED_BYTES_PER_CLIP_BYTE = 6
private const val MAX_SYNC_EVENT_ENVELOPE_BYTES = 64 * 1024
internal const val MAX_SYNC_PUSH_REQUEST_BYTES =
    Normalize.DEFAULT_MAX_CLIP_BYTES * MAX_JSON_ESCAPED_BYTES_PER_CLIP_BYTE +
        MAX_SYNC_EVENT_ENVELOPE_BYTES
// Query only a small metadata page, then reconstruct payloads one at a time in
// bounded chunks until the wire budget is full. The worker loops until the
// durable queue is drained; protocol batches remain compatible with the
// desktop's <=100-event limit.
private const val SYNC_OUTBOX_BATCH_LIMIT = 8
private const val OUTBOX_PAYLOAD_CHUNK_CHARS = 64 * 1024
private const val OUTBOX_EVENT_METADATA_BYTES = 1024
private const val EMPTY_SYNC_PUSH_REQUEST_BYTES = 13 // {"events":[]}

/** A durable outbox row cannot be represented safely in the sync protocol.
 * Keep it queued and persist a safe blocked marker instead of dropping it or
 * retrying a request that can never succeed. */
internal class SyncPushBlockedException(
    val seq: Long,
    val reason: SyncPushBlockReason,
) : IOException(reason.safeMessage)

internal fun nextPullCursorOrThrow(currentSince: Long, events: JSONArray, response: JSONObject): Long {
    return nextPullCursorOrThrow(
        currentSince = currentSince,
        eventCount = events.length(),
        nextSeq = response.optLong("next_seq", currentSince),
        hasMore = response.optBoolean("has_more", false),
    )
}

internal fun nextPullCursorOrThrow(
    currentSince: Long,
    eventCount: Int,
    nextSeq: Long,
    hasMore: Boolean,
): Long {
    if (
        nextSeq < currentSince ||
        (eventCount > 0 && nextSeq <= currentSince) ||
        (hasMore && nextSeq <= currentSince)
    ) {
        throw IOException("sync pull cursor did not advance")
    }
    return nextSeq
}

internal data class SyncPushBatch(
    val events: JSONArray,
    val maxSeq: Long,
    val sourceCount: Int,
)

internal fun buildSyncPushBatch(
    batch: List<OutboxEntity>,
    deviceId: String,
    maxRequestBytes: Int = MAX_SYNC_PUSH_REQUEST_BYTES,
): SyncPushBatch {
    require(maxRequestBytes > 0) { "maxRequestBytes must be positive" }

    val selected = mutableListOf<JSONObject>()
    var maxSeq = 0L
    var requestBytes = EMPTY_SYNC_PUSH_REQUEST_BYTES

    for (row in batch) {
        val data = try {
            JSONObject(row.payload)
        } catch (e: JSONException) {
            if (selected.isEmpty()) {
                throw SyncPushBlockedException(row.seq, SyncPushBlockReason.INVALID_PAYLOAD)
            }
            break
        }
        val event = JSONObject()
            .put("origin_device", deviceId)
            .put("seq", row.seq)
            .put("kind", row.kind)
            .put("ts", row.createdAt)
            .put("data", data)
        val eventBytes = event.toString().toByteArray(Charsets.UTF_8).size
        val separatorBytes = if (selected.isEmpty()) 0 else 1
        val remainingBytes = maxRequestBytes - requestBytes - separatorBytes

        if (eventBytes > remainingBytes) {
            if (selected.isEmpty()) {
                throw SyncPushBlockedException(row.seq, SyncPushBlockReason.EVENT_TOO_LARGE)
            }
            break
        }

        selected += event
        requestBytes += separatorBytes + eventBytes
        maxSeq = maxOf(maxSeq, row.seq)
    }

    return SyncPushBatch(
        events = JSONArray().apply { selected.forEach { put(it) } },
        maxSeq = maxSeq,
        sourceCount = selected.size,
    )
}

/** Reassemble only the payloads that can fit in the current request budget.
 * SQLite substr() is 1-based and counts Unicode code points, so chunk progress
 * uses codePointCount rather than Kotlin UTF-16 String.length. */
internal fun loadOutboxBatchFromChunks(
    metadata: List<OutboxMetadata>,
    readChunk: (seq: Long, offset: Int, charCount: Int) -> String?,
    maxBatchBytes: Int = MAX_SYNC_PUSH_REQUEST_BYTES,
): List<OutboxEntity> {
    require(maxBatchBytes > 0) { "maxBatchBytes must be positive" }
    val rows = mutableListOf<OutboxEntity>()
    var selectedBytes = 0L

    for (item in metadata) {
        if (item.payloadChars <= 0L || item.payloadBytes <= 0L) {
            throw SyncPushBlockedException(item.seq, SyncPushBlockReason.INVALID_PAYLOAD)
        }
        if (
            item.payloadChars > MAX_SYNC_PUSH_REQUEST_BYTES.toLong() ||
            item.payloadBytes > MAX_SYNC_PUSH_REQUEST_BYTES.toLong()
        ) {
            if (rows.isEmpty()) {
                throw SyncPushBlockedException(item.seq, SyncPushBlockReason.EVENT_TOO_LARGE)
            }
            break
        }

        val projectedBytes = selectedBytes + item.payloadBytes + OUTBOX_EVENT_METADATA_BYTES
        if (rows.isNotEmpty() && projectedBytes > maxBatchBytes.toLong()) break

        val payload = StringBuilder(item.payloadChars.toInt())
        var offset = 1
        var remainingChars = item.payloadChars
        while (remainingChars > 0L) {
            val requested = minOf(remainingChars, OUTBOX_PAYLOAD_CHUNK_CHARS.toLong()).toInt()
            val chunk = readChunk(item.seq, offset, requested)
                ?: throw SyncPushBlockedException(item.seq, SyncPushBlockReason.INVALID_PAYLOAD)
            val received = chunk.codePointCount(0, chunk.length)
            if (received != requested) {
                throw SyncPushBlockedException(item.seq, SyncPushBlockReason.INVALID_PAYLOAD)
            }
            payload.append(chunk)
            offset += received
            remainingChars -= received.toLong()
        }

        val payloadText = payload.toString()
        if (payloadText.toByteArray(Charsets.UTF_8).size.toLong() != item.payloadBytes) {
            throw SyncPushBlockedException(item.seq, SyncPushBlockReason.INVALID_PAYLOAD)
        }
        rows += OutboxEntity(
            seq = item.seq,
            kind = item.kind,
            payload = payloadText,
            createdAt = item.createdAt,
        )
        selectedBytes = projectedBytes
    }
    return rows
}

internal fun drainSyncOutbox(
    nextBatch: () -> List<OutboxEntity>,
    deviceId: String,
    push: (JSONArray) -> Long,
    clearUpTo: (Long) -> Unit,
    maxRequestBytes: Int = MAX_SYNC_PUSH_REQUEST_BYTES,
): Boolean {
    while (true) {
        val batch = nextBatch()
        if (batch.isEmpty()) break
        var candidateRows = batch
        var pushBatch = buildSyncPushBatch(candidateRows, deviceId, maxRequestBytes)
        var acked = -1L
        while (true) {
            try {
                acked = push(pushBatch.events)
                break
            } catch (e: SyncPushRequestTooLargeException) {
                if (pushBatch.sourceCount <= 1) {
                    throw SyncPushBlockedException(batch.first().seq, SyncPushBlockReason.EVENT_TOO_LARGE)
                }
                // A version-skewed desktop may accept each event but reject the
                // combined prefix under its older body cap. Retry a smaller
                // prefix without clearing anything; only a single-event 413 is
                // a durable block.
                candidateRows = candidateRows.take(maxOf(1, pushBatch.sourceCount / 2))
                pushBatch = buildSyncPushBatch(candidateRows, deviceId, maxRequestBytes)
            }
        }
        if (acked < 0) return false
        if (acked > pushBatch.maxSeq) {
            throw SyncPushBlockedException(batch.first().seq, SyncPushBlockReason.ACK_OUT_OF_RANGE)
        }
        // Clear only what the desktop explicitly acknowledged. If the server
        // detected a sequence gap, keeping later events preserves at-least-once
        // delivery and lets the next retry fill the hole.
        clearUpTo(acked)
        if (acked < pushBatch.maxSeq) return false
        if (pushBatch.sourceCount < batch.size) continue
    }
    return true
}

/** Run one push/pull cycle around a durable, content-safe blocked marker.
 *
 * The marker contains only a sequence and reason code. When the same row is
 * still at the queue head, the push phase is skipped completely, so periodic
 * workers do not repeatedly parse, serialize, or upload an impossible event.
 * Pull still runs. Deleting/replacing the head clears stale state automatically;
 * the UI can also clear it explicitly after a repair or app upgrade.
 */
internal fun runSyncCycle(
    firstPendingSeq: () -> Long?,
    readBlocked: () -> SyncPushBlockedState?,
    persistBlocked: (SyncPushBlockedState) -> Unit,
    clearBlocked: () -> Unit,
    pushPhase: () -> Boolean,
    pullPhase: () -> Boolean,
    onBlocked: () -> Unit = {},
): Boolean {
    val headSeq = firstPendingSeq()
    val blocked = readBlocked()
    val sameBlockedHead = blocked != null && blocked.seq == headSeq
    if (blocked != null && !sameBlockedHead) clearBlocked()

    var pushComplete = true
    if (!sameBlockedHead) {
        pushComplete = try {
            pushPhase()
        } catch (e: SyncPushBlockedException) {
            persistBlocked(SyncPushBlockedState(e.seq, e.reason))
            onBlocked()
            true // permanent local block: do not request an automatic retry
        }
    }

    val pullComplete = pullPhase()
    return pushComplete && pullComplete
}

/** Drains the outbox (push) then pulls desktop events (pull). Runs on demand
 * after a capture and periodically as a fallback. No foreground service —
 * self-use battery friendliness over instant delivery (ADR/PRODUCT_SPEC). */
class SyncWorker(ctx: Context, params: WorkerParameters) : Worker(ctx, params) {
    override fun doWork(): Result {
        val s = Settings(applicationContext)
        if (s.host.isNullOrBlank() || s.token.isNullOrBlank()) return Result.success()
        val db = ClipVaultApp.db(applicationContext)
        val client = SyncClient(s)
        return try {
            val complete = runSyncCycle(
                firstPendingSeq = { db.outbox().firstSeq() },
                readBlocked = { s.syncPushBlocked },
                persistBlocked = { state -> s.markSyncPushBlocked(state) },
                clearBlocked = { s.clearSyncPushBlocked() },
                pushPhase = {
                    val outbox = db.outbox()
                    drainSyncOutbox(
                        nextBatch = {
                            loadOutboxBatchFromChunks(
                                metadata = outbox.batchMetadata(SYNC_OUTBOX_BATCH_LIMIT),
                                readChunk = outbox::payloadChunk,
                            )
                        },
                        deviceId = s.deviceId,
                        push = { events -> client.push(events) },
                        clearUpTo = { seq -> outbox.clearUpTo(seq) },
                    )
                },
                pullPhase = { pullAll(db, s, client) },
                onBlocked = { Log.w("ClipVaultSync", "sync push blocked") },
            )
            if (complete) Result.success() else Result.retry()
        } catch (e: SyncAuthException) {
            // The paired desktop rejected the bearer token. Stop immediate
            // WorkManager retry loops and require an explicit re-pair instead
            // of repeatedly sending a known-bad token on every backoff attempt.
            s.token = null
            Log.w("ClipVaultSync", "sync auth failed")
            Result.success()
        } catch (e: Exception) {
            // Content-safe: log only the failure class. Raw exception messages can
            // include host/URL details from networking libraries.
            Log.w("ClipVaultSync", "sync failed: ${e.javaClass.simpleName}")
            Result.retry()
        }
    }

    private fun pullAll(db: AppDatabase, s: Settings, client: SyncClient): Boolean {
        var since = s.sinceSeq
        while (true) {
            val resp = client.pull(since) ?: return false
            val events = resp.getJSONArray("events")
            val nextSince = nextPullCursorOrThrow(since, events, resp)
            SyncApply.applyEvents(db, events)
            since = nextSince
            s.sinceSeq = since
            if (!resp.optBoolean("has_more", false)) return true
        }
    }
}

object SyncScheduler {
    fun requestPush(context: Context) {
        val req = OneTimeWorkRequestBuilder<SyncWorker>()
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .build()
        // APPEND_OR_REPLACE keeps an in-flight sync from being cancelled by a
        // burst of explicit saves. SyncWorker drains the durable outbox and
        // exits quickly when there is no work left, so a queued duplicate is a
        // safe reliability trade-off; cancellation would force push/pull to
        // restart mid-transfer.
        WorkManager.getInstance(context).enqueueUniqueWork("sync-now", ExistingWorkPolicy.APPEND_OR_REPLACE, req)
    }

    fun requestPushBestEffort(context: Context): Boolean {
        return try {
            requestPush(context)
            true
        } catch (e: Exception) {
            Log.w("ClipVaultSync", "sync schedule failed: ${e.javaClass.simpleName}")
            false
        }
    }

    fun schedulePeriodic(context: Context) {
        val req = PeriodicWorkRequestBuilder<SyncWorker>(15, TimeUnit.MINUTES)
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .build()
        // KEEP: one periodic worker total. Without a unique policy, every app open
        // stacked another worker (battery/resource leak).
        WorkManager.getInstance(context)
            .enqueueUniquePeriodicWork("sync-periodic", ExistingPeriodicWorkPolicy.KEEP, req)
    }
}
