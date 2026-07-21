package com.clipvault.app.capture

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CaptureWorkerLauncherTest {
    @Test
    fun startFailureRunsCleanupExactlyOnce() {
        var cleanups = 0

        val started = tryStartCaptureWorker(
            startThread = { throw IllegalStateException("forced start failure") },
            worker = { throw AssertionError("worker must not run") },
            onStartFailure = { cleanups += 1 },
        )

        assertFalse(started)
        assertEquals(1, cleanups)
    }

    @Test
    fun successfulStartDoesNotRunFailureCleanup() {
        var scheduledWorker: (() -> Unit)? = null
        var workerRuns = 0
        var cleanups = 0

        val started = tryStartCaptureWorker(
            startThread = { scheduledWorker = it },
            worker = { workerRuns += 1 },
            onStartFailure = { cleanups += 1 },
        )

        assertTrue(started)
        assertNotNull(scheduledWorker)
        assertEquals(0, cleanups)
        scheduledWorker?.invoke()
        assertEquals(1, workerRuns)
    }

    @Test
    fun fatalStartErrorStillRunsCleanupBeforePropagation() {
        var cleanups = 0

        try {
            tryStartCaptureWorker(
                startThread = { throw AssertionError("forced fatal failure") },
                worker = {},
                onStartFailure = { cleanups += 1 },
            )
            throw AssertionError("fatal error must propagate")
        } catch (error: AssertionError) {
            assertEquals("forced fatal failure", error.message)
        }
        assertEquals(1, cleanups)
    }
}
