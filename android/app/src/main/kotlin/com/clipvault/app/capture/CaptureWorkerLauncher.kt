package com.clipvault.app.capture

/**
 * Starts an ingest worker while guaranteeing cleanup if thread construction or
 * Thread.start() fails. Fatal Errors still propagate after cleanup.
 */
internal fun tryStartCaptureWorker(
    startThread: (() -> Unit) -> Unit,
    worker: () -> Unit,
    onStartFailure: () -> Unit,
): Boolean {
    var started = false
    try {
        startThread(worker)
        started = true
    } catch (_: Exception) {
        return false
    } finally {
        if (!started) onStartFailure()
    }
    return true
}
