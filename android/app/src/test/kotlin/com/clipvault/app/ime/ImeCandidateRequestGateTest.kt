package com.clipvault.app.ime

import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ImeCandidateRequestGateTest {
    @Test
    fun newestRequestSupersedesQueuedAndRenderedRequests() {
        val gate = ImeCandidateRequestGate()
        val input = gate.beginInput()
        val first = requireNotNull(gate.beginRequest(input))
        assertTrue(gate.isCurrent(first))

        val second = requireNotNull(gate.beginRequest(input))

        assertFalse(gate.isCurrent(first))
        assertTrue(gate.isCurrent(second))
    }

    @Test
    fun newEditorInvalidatesCompletedRequestAndRejectsOldSession() {
        val gate = ImeCandidateRequestGate()
        val firstInput = gate.beginInput()
        val rendered = requireNotNull(gate.beginRequest(firstInput))

        val secondInput = gate.beginInput()

        assertFalse(gate.isCurrent(rendered))
        assertNull(gate.beginRequest(firstInput))
        assertTrue(gate.isCurrent(requireNotNull(gate.beginRequest(secondInput))))
    }

    @Test
    fun finishInputInvalidatesPostedAndRenderedRequests() {
        val gate = ImeCandidateRequestGate()
        val input = gate.beginInput()
        val posted = requireNotNull(gate.beginRequest(input))

        gate.endInput()

        assertFalse(gate.isCurrent(posted))
        assertNull(gate.beginRequest(input))
    }

    @Test
    fun destroyPermanentlyFailsClosed() {
        val gate = ImeCandidateRequestGate()
        val input = gate.beginInput()
        val inFlight = requireNotNull(gate.beginRequest(input))

        gate.destroy()
        val afterDestroy = gate.beginInput()

        assertFalse(gate.isCurrent(inFlight))
        assertNull(gate.beginRequest(input))
        assertNull(gate.beginRequest(afterDestroy))
    }
}
