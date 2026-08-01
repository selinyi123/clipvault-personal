package com.clipvault.app.runtime

import android.app.Service
import android.content.Intent
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.Message
import android.os.Messenger
import com.clipvault.ime.engine.RuntimeSnapshotContract
import com.clipvault.ime.engine.RuntimeSnapshotItem
import java.util.UUID
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong

/**
 * Signature-protected, prefix-free candidate snapshot bridge.
 * It accepts no typed text and performs no work on an IME key path.
 */
class ClipVaultSnapshotBrokerService : Service() {
    private val facade by lazy { ClipVaultRuntime.facade(this) }
    private val publisherEpoch = UUID.randomUUID().toString()
    private val snapshotGeneration = AtomicLong(0)
    private val executor = ThreadPoolExecutor(
        1,
        1,
        0L,
        TimeUnit.MILLISECONDS,
        ArrayBlockingQueue(1),
        ThreadPoolExecutor.AbortPolicy(),
    )
    private val messenger = Messenger(
        Handler(Looper.getMainLooper()) { message ->
            if (message.what != RuntimeSnapshotContract.REQUEST_SNAPSHOT) return@Handler false
            val reply = message.replyTo ?: return@Handler true
            val requestId = message.data.getLong(RuntimeSnapshotContract.KEY_REQUEST_ID, 0L)
            val inputSessionGeneration = message.data.getLong(
                RuntimeSnapshotContract.KEY_INPUT_SESSION_GENERATION,
                0L,
            )
            if (requestId <= 0L || inputSessionGeneration <= 0L) return@Handler true
            val limit = message.data.getInt(RuntimeSnapshotContract.KEY_LIMIT, 8)
                .coerceIn(1, RuntimeSnapshotContract.MAX_ITEMS)
            val startedAt = android.os.SystemClock.elapsedRealtime()
            val task = Runnable {
                if (android.os.SystemClock.elapsedRealtime() - startedAt >
                    RuntimeSnapshotContract.REQUEST_TIMEOUT_MS
                ) {
                    sendSnapshot(reply, requestId, inputSessionGeneration, emptyList())
                    return@Runnable
                }
                val candidates = try {
                    RuntimeSnapshotBrokerPolicy.prepare(
                        candidates = facade.listCandidates(limit = limit),
                        limit = limit,
                    )
                } catch (_: RuntimeException) {
                    emptyList()
                }
                if (android.os.SystemClock.elapsedRealtime() - startedAt >
                    RuntimeSnapshotContract.REQUEST_TIMEOUT_MS
                ) {
                    sendSnapshot(reply, requestId, inputSessionGeneration, emptyList())
                } else {
                    sendSnapshot(reply, requestId, inputSessionGeneration, candidates)
                }
            }
            try {
                executor.execute(task)
            } catch (_: RejectedExecutionException) {
                // Saturation is an explicit empty surface, never a silent stale snapshot.
                sendSnapshot(reply, requestId, inputSessionGeneration, emptyList())
            }
            true
        },
    )

    private fun sendSnapshot(
        reply: Messenger,
        requestId: Long,
        inputSessionGeneration: Long,
        candidates: List<RuntimeSnapshotItem>,
    ) {
        val generation = snapshotGeneration.incrementAndGet()
        if (generation <= 0L) return
        val response = Message.obtain(null, RuntimeSnapshotContract.RESPONSE_SNAPSHOT).apply {
            data.putLong(RuntimeSnapshotContract.KEY_REQUEST_ID, requestId)
            data.putLong(
                RuntimeSnapshotContract.KEY_INPUT_SESSION_GENERATION,
                inputSessionGeneration,
            )
            data.putString(RuntimeSnapshotContract.KEY_PUBLISHER_EPOCH, publisherEpoch)
            data.putLong(RuntimeSnapshotContract.KEY_SNAPSHOT_GENERATION, generation)
            data.putLong(
                RuntimeSnapshotContract.KEY_EXPIRES_AT_ELAPSED_MS,
                android.os.SystemClock.elapsedRealtime() +
                    RuntimeSnapshotContract.MAX_SNAPSHOT_LIFETIME_MS,
            )
            data.putStringArrayList(
                RuntimeSnapshotContract.KEY_CANDIDATE_IDS,
                ArrayList(candidates.map { it.candidateId }),
            )
            data.putStringArrayList(
                RuntimeSnapshotContract.KEY_SOURCES,
                ArrayList(candidates.map { it.source }),
            )
            data.putStringArrayList(
                RuntimeSnapshotContract.KEY_LABELS,
                ArrayList(candidates.map { it.label }),
            )
            data.putStringArrayList(
                RuntimeSnapshotContract.KEY_TEXTS,
                ArrayList(candidates.map { it.text }),
            )
        }
        try {
            reply.send(response)
        } catch (_: android.os.RemoteException) {
            // The isolated IME disappeared; the snapshot is discarded.
        }
    }

    override fun onBind(intent: Intent?): IBinder = messenger.binder

    override fun onDestroy() {
        executor.shutdownNow()
        super.onDestroy()
    }
}
