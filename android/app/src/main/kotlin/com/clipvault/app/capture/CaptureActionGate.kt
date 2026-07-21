package com.clipvault.app.capture

import android.os.SystemClock
import java.util.UUID

/**
 * Process-local guard for explicit clipboard-capture actions.
 *
 * Only the latest ID explicitly issued by the TileService may be consumed,
 * each ID is consumed at most once, and only one capture may remain in flight
 * across Activity instances. Restored or redelivered Intents therefore cannot
 * read a newer clipboard value later.
 */
internal class CaptureActionGate(private val maxPendingAgeMs: Long = 10_000L) {
    private data class PendingAction(val id: String, val issuedAtMs: Long)

    private var pendingAction: PendingAction? = null
    private var activeActionId: String? = null

    init {
        require(maxPendingAgeMs > 0L)
    }

    @Synchronized
    fun issue(actionId: String, issuedAtMs: Long) {
        require(actionId.isNotBlank())
        require(issuedAtMs >= 0L)
        if (activeActionId != null) {
            // A click that occurs during an active ingest is deliberately
            // dropped now; it must not become eligible after the worker exits.
            pendingAction = null
            return
        }
        pendingAction = PendingAction(actionId, issuedAtMs)
    }

    @Synchronized
    fun tryAcquire(actionId: String, nowMs: Long): Boolean {
        val pending = pendingAction ?: return false
        if (pending.id != actionId) return false

        // Consume before checking active state so a rapid second launch cannot
        // become eligible later merely because the first worker completed.
        pendingAction = null
        if (activeActionId != null) return false
        val ageMs = nowMs - pending.issuedAtMs
        if (ageMs !in 0L..maxPendingAgeMs) return false

        activeActionId = actionId
        return true
    }

    @Synchronized
    fun cancelPending(actionId: String) {
        if (pendingAction?.id == actionId) pendingAction = null
    }

    @Synchronized
    fun release(actionId: String) {
        if (activeActionId == actionId) activeActionId = null
    }
}

/** Shared process-level coordinator used by the TileService and capture Activity. */
internal object ClipboardCaptureActions {
    private val gate = CaptureActionGate()

    fun issue(): String = UUID.randomUUID().toString().also {
        gate.issue(it, SystemClock.elapsedRealtime())
    }

    fun tryAcquire(actionId: String): Boolean =
        gate.tryAcquire(actionId, SystemClock.elapsedRealtime())

    fun cancelPending(actionId: String) = gate.cancelPending(actionId)

    fun release(actionId: String) = gate.release(actionId)
}
