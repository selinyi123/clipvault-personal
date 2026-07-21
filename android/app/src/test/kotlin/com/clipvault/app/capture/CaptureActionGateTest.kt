package com.clipvault.app.capture

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CaptureActionGateTest {
    @Test
    fun rejectsConcurrentActionsAndConsumedIntentReplay() {
        val gate = CaptureActionGate()

        gate.issue("action-1", issuedAtMs = 10L)
        assertTrue(gate.tryAcquire("action-1", nowMs = 11L))
        gate.issue("action-2", issuedAtMs = 12L)
        assertFalse(gate.tryAcquire("action-2", nowMs = 13L))

        gate.release("action-1")
        assertFalse(gate.tryAcquire("action-2", nowMs = 14L))
        assertFalse(gate.tryAcquire("action-1", nowMs = 14L))
        gate.issue("action-3", issuedAtMs = 15L)
        assertTrue(gate.tryAcquire("action-3", nowMs = 16L))
    }

    @Test
    fun onlyLatestIssuedActionCanAcquire() {
        val gate = CaptureActionGate()

        gate.issue("action-1", issuedAtMs = 10L)
        gate.issue("action-2", issuedAtMs = 11L)
        assertFalse(gate.tryAcquire("action-1", nowMs = 12L))
        assertTrue(gate.tryAcquire("action-2", nowMs = 12L))
    }

    @Test
    fun wrongTokenCannotConsumeLatestPendingAction() {
        val gate = CaptureActionGate()

        gate.issue("latest-action", issuedAtMs = 10L)
        assertFalse(gate.tryAcquire("wrong-action", nowMs = 11L))
        assertTrue(gate.tryAcquire("latest-action", nowMs = 11L))
    }

    @Test
    fun wrongReleaseCannotClearAnotherAction() {
        val gate = CaptureActionGate()

        gate.issue("action-1", issuedAtMs = 10L)
        assertTrue(gate.tryAcquire("action-1", nowMs = 11L))
        gate.release("different-action")
        gate.issue("action-2", issuedAtMs = 12L)
        assertFalse(gate.tryAcquire("action-2", nowMs = 13L))

        gate.release("action-1")
        assertFalse(gate.tryAcquire("action-2", nowMs = 14L))
    }

    @Test
    fun cancelledPendingActionCannotReadLaterClipboard() {
        val gate = CaptureActionGate()

        gate.issue("action-1", issuedAtMs = 10L)
        gate.cancelPending("action-1")
        assertFalse(gate.tryAcquire("action-1", nowMs = 11L))
    }

    @Test
    fun staleOrClockInvalidActionsFailClosed() {
        val gate = CaptureActionGate(maxPendingAgeMs = 100L)

        gate.issue("stale", issuedAtMs = 10L)
        assertFalse(gate.tryAcquire("stale", nowMs = 111L))

        gate.issue("clock-reset", issuedAtMs = 20L)
        assertFalse(gate.tryAcquire("clock-reset", nowMs = 19L))
    }
}
