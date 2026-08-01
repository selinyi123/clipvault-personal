package com.clipvault.imeapp

internal fun interface RepeatCancellation {
    fun cancel()
}

internal fun interface RepeatScheduler {
    fun schedule(delayMs: Long, action: () -> Unit): RepeatCancellation
}

/** One immediate delete followed by a bounded, cancellable hold cadence. */
internal class BackspaceRepeatController(
    private val scheduler: RepeatScheduler,
    private val deleteOnce: () -> Boolean,
    private val initialDelayMs: Long = 360L,
    private val repeatIntervalMs: Long = 55L,
    private val maximumDeletesPerPress: Int = 120,
) {
    private var pressed = false
    private var deleteCount = 0
    private var pending: RepeatCancellation? = null

    init {
        require(initialDelayMs in 200L..1_000L)
        require(repeatIntervalMs in 30L..250L)
        require(maximumDeletesPerPress in 1..500)
    }

    fun press(): Boolean {
        if (pressed) return false
        pressed = true
        deleteCount = 0
        if (!emitDelete()) {
            release()
            return false
        }
        schedule(initialDelayMs)
        return true
    }

    fun release() {
        pressed = false
        pending?.cancel()
        pending = null
        deleteCount = 0
    }

    private fun emitDelete(): Boolean {
        if (!pressed || deleteCount >= maximumDeletesPerPress || !deleteOnce()) return false
        deleteCount += 1
        return true
    }

    private fun schedule(delayMs: Long) {
        pending?.cancel()
        pending = scheduler.schedule(delayMs) {
            pending = null
            if (!pressed) return@schedule
            if (!emitDelete() || deleteCount >= maximumDeletesPerPress) {
                release()
            } else {
                schedule(repeatIntervalMs)
            }
        }
    }
}
