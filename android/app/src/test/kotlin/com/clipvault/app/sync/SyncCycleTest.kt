package com.clipvault.app.sync

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SyncCycleTest {
    @Test
    fun blockedSequencePersistsAcrossInvocationsAndPullStillRuns() {
        var headSeq: Long? = 7L
        var blocked: SyncPushBlockedState? = null
        var pushCalls = 0
        var pullCalls = 0
        var markerWrites = 0

        fun invoke(): Boolean = runSyncCycle(
            firstPendingSeq = { headSeq },
            readBlocked = { blocked },
            persistBlocked = {
                markerWrites += 1
                blocked = it
            },
            clearBlocked = { blocked = null },
            pushPhase = {
                pushCalls += 1
                throw SyncPushBlockedException(7L, SyncPushBlockReason.EVENT_TOO_LARGE)
            },
            pullPhase = {
                pullCalls += 1
                true
            },
        )

        assertTrue(invoke())
        assertEquals(SyncPushBlockedState(7L, SyncPushBlockReason.EVENT_TOO_LARGE), blocked)
        assertEquals(1, pushCalls)
        assertEquals(1, pullCalls)
        assertEquals(1, markerWrites)

        // Simulate a later periodic/explicit worker invocation against the same
        // durable marker and queue head. Push preparation is skipped entirely.
        assertTrue(invoke())
        assertEquals(1, pushCalls)
        assertEquals(2, pullCalls)
        assertEquals(1, markerWrites)
        assertEquals(7L, headSeq)
    }

    @Test
    fun removingBlockedRowClearsMarkerAndResumesPushAutomatically() {
        var headSeq: Long? = 9L
        var blocked: SyncPushBlockedState? =
            SyncPushBlockedState(9L, SyncPushBlockReason.INVALID_PAYLOAD)
        var pushCalls = 0
        var clearCalls = 0

        // The blocked row was repaired by removal/replacement; the next durable
        // sequence is now at the head.
        headSeq = 10L
        val complete = runSyncCycle(
            firstPendingSeq = { headSeq },
            readBlocked = { blocked },
            persistBlocked = { blocked = it },
            clearBlocked = {
                clearCalls += 1
                blocked = null
            },
            pushPhase = {
                pushCalls += 1
                true
            },
            pullPhase = { true },
        )

        assertTrue(complete)
        assertNull(blocked)
        assertEquals(1, clearCalls)
        assertEquals(1, pushCalls)
    }

    @Test
    fun explicitRecheckLetsRepairedSameSequenceRunOnce() {
        var blocked: SyncPushBlockedState? =
            SyncPushBlockedState(12L, SyncPushBlockReason.INVALID_PAYLOAD)
        var pushCalls = 0

        // Mirrors the local UI's explicit "recheck" action after the user has
        // repaired the row or installed an update that can encode it.
        blocked = null
        val complete = runSyncCycle(
            firstPendingSeq = { 12L },
            readBlocked = { blocked },
            persistBlocked = { blocked = it },
            clearBlocked = { blocked = null },
            pushPhase = {
                pushCalls += 1
                true
            },
            pullPhase = { true },
        )

        assertTrue(complete)
        assertEquals(1, pushCalls)
        assertNull(blocked)
    }

    @Test
    fun blockedPushDoesNotHidePullRetryState() {
        val blocked = SyncPushBlockedState(4L, SyncPushBlockReason.ACK_OUT_OF_RANGE)
        var pullCalls = 0

        val complete = runSyncCycle(
            firstPendingSeq = { 4L },
            readBlocked = { blocked },
            persistBlocked = { error("must not rewrite an unchanged marker") },
            clearBlocked = { error("must not clear an unchanged marker") },
            pushPhase = { error("must not process an unchanged blocked sequence") },
            pullPhase = {
                pullCalls += 1
                false
            },
        )

        assertFalse(complete)
        assertEquals(1, pullCalls)
    }
}
