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
import com.clipvault.app.data.OutboxEntity
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

internal const val MAX_SYNC_PUSH_REQUEST_BYTES = 3 * 1024 * 1024
private const val SYNC_OUTBOX_BATCH_LIMIT = 100

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

    for (row in batch) {
        val event = JSONObject()
            .put("origin_device", deviceId)
            .put("seq", row.seq)
            .put("kind", row.kind)
            .put("ts", row.createdAt)
            .put("data", JSONObject(row.payload))

        if (selected.isNotEmpty() && syncPushRequestBytes(selected + event) > maxRequestBytes) {
            break
        }

        selected += event
        maxSeq = maxOf(maxSeq, row.seq)
    }

    return SyncPushBatch(
        events = JSONArray().apply { selected.forEach { put(it) } },
        maxSeq = maxSeq,
        sourceCount = selected.size,
    )
}

private fun syncPushRequestBytes(events: List<JSONObject>): Int {
    val array = JSONArray().apply { events.forEach { put(it) } }
    return JSONObject()
        .put("events", array)
        .toString()
        .toByteArray(Charsets.UTF_8)
        .size
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
        val pushBatch = buildSyncPushBatch(batch, deviceId, maxRequestBytes)
        val acked = push(pushBatch.events)
        if (acked < 0) return false
        // Clear only what the desktop explicitly acknowledged. If the server
        // detected a sequence gap, keeping later events preserves at-least-once
        // delivery and lets the next retry fill the hole.
        clearUpTo(acked)
        if (acked < pushBatch.maxSeq) return false
        if (pushBatch.sourceCount < batch.size) continue
        if (batch.size < SYNC_OUTBOX_BATCH_LIMIT) break
    }
    return true
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
            // push outbox in batches
            val pushed = drainSyncOutbox(
                nextBatch = { db.outbox().batch(SYNC_OUTBOX_BATCH_LIMIT) },
                deviceId = s.deviceId,
                push = { events -> client.push(events) },
                clearUpTo = { seq -> db.outbox().clearUpTo(seq) },
            )
            if (!pushed) return Result.retry()
            // pull desktop events
            var since = s.sinceSeq
            while (true) {
                val resp = client.pull(since) ?: return Result.retry()
                val events = resp.getJSONArray("events")
                val nextSince = nextPullCursorOrThrow(since, events, resp)
                SyncApply.applyEvents(db, events)
                since = nextSince
                s.sinceSeq = since
                if (!resp.optBoolean("has_more", false)) break
            }
            Result.success()
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
