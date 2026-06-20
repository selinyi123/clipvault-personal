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
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

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
            while (true) {
                val batch = db.outbox().batch(100)
                if (batch.isEmpty()) break
                val events = JSONArray()
                var maxSeq = 0L
                batch.forEach { e ->
                    events.put(JSONObject()
                        .put("origin_device", s.deviceId).put("seq", e.seq)
                        .put("kind", e.kind).put("ts", e.createdAt)
                        .put("data", JSONObject(e.payload)))
                    maxSeq = maxOf(maxSeq, e.seq)
                }
                val acked = client.push(events)
                if (acked < 0) return Result.retry()
                db.outbox().clearUpTo(maxSeq)
                if (batch.size < 100) break
            }
            // pull desktop events
            var since = s.sinceSeq
            while (true) {
                val resp = client.pull(since) ?: return Result.retry()
                val events = resp.getJSONArray("events")
                SyncApply.applyEvents(db, events)
                since = resp.optLong("next_seq", since)
                s.sinceSeq = since
                if (!resp.optBoolean("has_more", false)) break
            }
            Result.success()
        } catch (e: Exception) {
            // Content-safe: log the failure class/message (never clip content) so
            // sync problems are diagnosable instead of silently retrying forever.
            Log.w("ClipVaultSync", "sync failed: ${e.javaClass.simpleName}: ${e.message}")
            Result.retry()
        }
    }
}

object SyncScheduler {
    fun requestPush(context: Context) {
        val req = OneTimeWorkRequestBuilder<SyncWorker>()
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork("sync-now", ExistingWorkPolicy.REPLACE, req)
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
