package com.clipvault.imeapp

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.Message
import android.os.Messenger
import android.os.SystemClock
import com.clipvault.ime.engine.RuntimeSnapshotContract

internal data class RuntimeCandidateSnapshot(
    val publisherEpoch: String,
    val snapshotGeneration: Long,
    val expiresAtElapsedMs: Long,
    val candidateId: String,
    val source: String,
    val label: String,
    val text: String,
)

/** Optional, signature-protected Binder snapshot client; never sends key text. */
internal class RuntimeSnapshotClient(private val context: Context) {
    private var remote: Messenger? = null
    private val mainHandler = Handler(Looper.getMainLooper())
    private var inputSessionGeneration = 1L
    private var nextRequestId = 1L
    private var publisherEpoch: String? = null
    private var lastSnapshotGeneration = 0L

    private data class Pending(
        val inputSessionGeneration: Long,
        val callback: (List<RuntimeCandidateSnapshot>) -> Unit,
    )

    private val pending = mutableMapOf<Long, Pending>()
    private var onConnected: (() -> Unit)? = null
    private var onInvalidated: (() -> Unit)? = null
    private val reply = Messenger(Handler(Looper.getMainLooper()) { message ->
        if (message.what != RuntimeSnapshotContract.RESPONSE_SNAPSHOT) return@Handler false
        val requestId = message.data.getLong(RuntimeSnapshotContract.KEY_REQUEST_ID, 0L)
        val responseInputSessionGeneration = message.data.getLong(
            RuntimeSnapshotContract.KEY_INPUT_SESSION_GENERATION,
            0L,
        )
        val callback = pending.remove(requestId)
        if (
            callback == null ||
            !RuntimeSnapshotContract.responseMatchesInputSession(
                currentInputSessionGeneration = inputSessionGeneration,
                pendingInputSessionGeneration = callback.inputSessionGeneration,
                responseInputSessionGeneration = responseInputSessionGeneration,
            )
        ) return@Handler true

        val responsePublisherEpoch = message.data.getString(
            RuntimeSnapshotContract.KEY_PUBLISHER_EPOCH,
        ).orEmpty()
        val responseSnapshotGeneration = message.data.getLong(
            RuntimeSnapshotContract.KEY_SNAPSHOT_GENERATION,
            0L,
        )
        val expiresAtElapsedMs = message.data.getLong(
            RuntimeSnapshotContract.KEY_EXPIRES_AT_ELAPSED_MS,
            0L,
        )
        val candidateIds = message.data.getStringArrayList(
            RuntimeSnapshotContract.KEY_CANDIDATE_IDS,
        ).orEmpty()
        val sources = message.data.getStringArrayList(
            RuntimeSnapshotContract.KEY_SOURCES,
        ).orEmpty()
        val labels = message.data.getStringArrayList(RuntimeSnapshotContract.KEY_LABELS).orEmpty()
        val texts = message.data.getStringArrayList(RuntimeSnapshotContract.KEY_TEXTS).orEmpty()
        val decoded = RuntimeSnapshotContract.decodeFrame(
            publisherEpoch = responsePublisherEpoch,
            snapshotGeneration = responseSnapshotGeneration,
            expiresAtElapsedMs = expiresAtElapsedMs,
            candidateIds = candidateIds,
            sources = sources,
            labels = labels,
            texts = texts,
        )
        if (
            decoded == null ||
            !RuntimeSnapshotContract.isFreshExpiry(SystemClock.elapsedRealtime(), expiresAtElapsedMs)
        ) {
            callback.callback(emptyList())
            return@Handler true
        }

        val currentPublisher = publisherEpoch
        if (
            !RuntimeSnapshotContract.acceptsSnapshotIdentity(
                currentPublisherEpoch = currentPublisher,
                lastSnapshotGeneration = lastSnapshotGeneration,
                incomingPublisherEpoch = decoded.publisherEpoch,
                incomingSnapshotGeneration = decoded.snapshotGeneration,
            )
        ) {
            if (currentPublisher != null && currentPublisher != decoded.publisherEpoch) {
                clearPublisherState()
                onInvalidated?.invoke()
            }
            callback.callback(emptyList())
            return@Handler true
        }
        publisherEpoch = decoded.publisherEpoch
        lastSnapshotGeneration = decoded.snapshotGeneration
        callback.callback(
            decoded.items.map { item ->
                RuntimeCandidateSnapshot(
                    publisherEpoch = decoded.publisherEpoch,
                    snapshotGeneration = decoded.snapshotGeneration,
                    expiresAtElapsedMs = decoded.expiresAtElapsedMs,
                    candidateId = item.candidateId,
                    source = item.source,
                    label = item.label,
                    text = item.text,
                )
            },
        )
        true
    })

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            remote = binder?.let(::Messenger)
            cancelPending()
            clearPublisherState()
            onInvalidated?.invoke()
            if (remote != null) onConnected?.invoke() else invalidateConnection()
        }

        override fun onServiceDisconnected(name: ComponentName?) = invalidateConnection()

        override fun onBindingDied(name: ComponentName?) = invalidateConnection()

        override fun onNullBinding(name: ComponentName?) = invalidateConnection()
    }

    fun bind(onConnected: () -> Unit, onInvalidated: () -> Unit): Boolean {
        this.onConnected = onConnected
        this.onInvalidated = onInvalidated
        val bound = context.bindService(
            Intent().setComponent(
                ComponentName(
                    RuntimeSnapshotContract.RUNTIME_PACKAGE,
                    RuntimeSnapshotContract.RUNTIME_SERVICE,
                ),
            ),
            connection,
            Context.BIND_AUTO_CREATE,
        )
        if (!bound) onInvalidated()
        return bound
    }

    fun request(limit: Int, callback: (List<RuntimeCandidateSnapshot>) -> Unit): Boolean {
        val target = remote
        if (target == null) {
            callback(emptyList())
            return false
        }
        pending.clear()
        val requestId = nextRequestId.also {
            nextRequestId = if (it == Long.MAX_VALUE) 1L else it + 1L
        }
        val requestInputSessionGeneration = inputSessionGeneration
        val message = Message.obtain(null, RuntimeSnapshotContract.REQUEST_SNAPSHOT).apply {
            data.putInt(
                RuntimeSnapshotContract.KEY_LIMIT,
                limit.coerceIn(1, RuntimeSnapshotContract.MAX_ITEMS),
            )
            data.putLong(RuntimeSnapshotContract.KEY_REQUEST_ID, requestId)
            data.putLong(
                RuntimeSnapshotContract.KEY_INPUT_SESSION_GENERATION,
                requestInputSessionGeneration,
            )
            replyTo = reply
        }
        return try {
            pending[requestId] = Pending(requestInputSessionGeneration, callback)
            mainHandler.postDelayed(
                {
                    val expired = pending.remove(requestId) ?: return@postDelayed
                    if (
                        RuntimeSnapshotContract.responseMatchesInputSession(
                            currentInputSessionGeneration = inputSessionGeneration,
                            pendingInputSessionGeneration = expired.inputSessionGeneration,
                            responseInputSessionGeneration = expired.inputSessionGeneration,
                        )
                    ) expired.callback(emptyList())
                },
                RuntimeSnapshotContract.REQUEST_TIMEOUT_MS,
            )
            target.send(message)
            true
        } catch (_: android.os.RemoteException) {
            pending.remove(requestId)?.callback?.invoke(emptyList())
            false
        }
    }

    fun cancelPending() {
        inputSessionGeneration = if (inputSessionGeneration == Long.MAX_VALUE) {
            1L
        } else {
            inputSessionGeneration + 1L
        }
        pending.clear()
    }

    fun unbind() {
        runCatching { context.unbindService(connection) }
        remote = null
        onConnected = null
        onInvalidated = null
        clearPublisherState()
        cancelPending()
    }

    private fun invalidateConnection() {
        remote = null
        val callbacks = pending.values.map { it.callback }
        pending.clear()
        cancelPending()
        clearPublisherState()
        callbacks.forEach { it(emptyList()) }
        onInvalidated?.invoke()
    }

    private fun clearPublisherState() {
        publisherEpoch = null
        lastSnapshotGeneration = 0L
    }
}
