package com.clipvault.app.sync

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException

class SyncPairingStateGateTest {
    @Test
    fun defaultGatesCannotExposeMixedEndpointAndTokenDuringReplacement() {
        // Use production defaults, not an explicitly shared test state: this
        // proves separate Settings gates share the same process monitor.
        val writerGate = SyncPairingStateGate()
        val readerGate = SyncPairingStateGate()
        val replacementPaused = CountDownLatch(1)
        val finishReplacement = CountDownLatch(1)
        val readerAttempted = CountDownLatch(1)
        val executor = Executors.newFixedThreadPool(2)
        var host = "old.desktop"
        var token: String? = "old-token"

        try {
            val writer = executor.submit {
                writerGate.replaceEndpoint {
                    token = null
                    replacementPaused.countDown()
                    assertTrue(finishReplacement.await(2, TimeUnit.SECONDS))
                    host = "new.desktop"
                    token = "new-token"
                }
            }
            assertTrue(replacementPaused.await(2, TimeUnit.SECONDS))

            val reader = executor.submit<SyncRequestSnapshot> {
                readerAttempted.countDown()
                readerGate.snapshot { revision, endpointRevision ->
                    request(host, token, revision, endpointRevision)
                }
            }
            assertTrue(readerAttempted.await(2, TimeUnit.SECONDS))
            try {
                reader.get(150, TimeUnit.MILLISECONDS)
                fail("snapshot must wait for the in-progress replacement")
            } catch (_: TimeoutException) {
                // Expected: both gate instances share the production monitor.
            }

            finishReplacement.countDown()
            writer.get(2, TimeUnit.SECONDS)
            val snapshot = reader.get(2, TimeUnit.SECONDS)
            assertEquals("new.desktop", snapshot.host)
            assertEquals("new-token", snapshot.bearerToken)
        } finally {
            finishReplacement.countDown()
            executor.shutdownNow()
        }
    }

    @Test
    fun simulatedNetworkWaitAfterSnapshotDoesNotBlockReplacement() {
        val processState = SyncPairingProcessState()
        val requestGate = SyncPairingStateGate(processState)
        val writerGate = SyncPairingStateGate(processState)
        val snapshotTaken = CountDownLatch(1)
        val finishNetwork = CountDownLatch(1)
        val executor = Executors.newFixedThreadPool(2)
        var host = "old.desktop"
        var token: String? = "old-token"

        try {
            val request = executor.submit<SyncRequestSnapshot> {
                val snapshot = requestGate.snapshot { revision, endpointRevision ->
                    request(host, token, revision, endpointRevision)
                }
                snapshotTaken.countDown()
                assertTrue(finishNetwork.await(2, TimeUnit.SECONDS))
                snapshot
            }
            assertTrue(snapshotTaken.await(2, TimeUnit.SECONDS))

            val replacement = executor.submit {
                writerGate.replaceEndpoint {
                    host = "new.desktop"
                    token = "new-token"
                }
            }
            replacement.get(1, TimeUnit.SECONDS)
            finishNetwork.countDown()

            val oldRequest = request.get(2, TimeUnit.SECONDS)
            assertEquals("old.desktop", oldRequest.host)
            assertEquals("old-token", oldRequest.bearerToken)
        } finally {
            finishNetwork.countDown()
            executor.shutdownNow()
        }
    }

    @Test
    fun lateAuthFailureCannotClearFreshPairingOrApplyOldSuccess() {
        val processState = SyncPairingProcessState()
        val gate = SyncPairingStateGate(processState)
        var host = "old.desktop"
        var token: String? = "old-token"
        var oldSuccessEffects = 0
        val oldRequest = gate.snapshot { revision, endpointRevision ->
            request(host, token, revision, endpointRevision)
        }

        gate.replaceEndpoint {
            host = "new.desktop"
            token = "new-token"
        }

        val staleCleared = gate.clearRejectedIfCurrent(
            expected = oldRequest,
            currentStoreMatches = { host == oldRequest.host },
            clear = { token = null },
        )
        val staleApplied = gate.runIfCurrent(
            expected = oldRequest,
            currentStoreMatches = { host == oldRequest.host },
            block = { oldSuccessEffects += 1 },
        )
        assertFalse(staleCleared)
        assertFalse(staleApplied)
        assertEquals("new-token", token)
        assertEquals(0, oldSuccessEffects)

        val currentRequest = gate.snapshot { revision, endpointRevision ->
            request(host, token, revision, endpointRevision)
        }
        val currentCleared = gate.clearRejectedIfCurrent(
            expected = currentRequest,
            currentStoreMatches = { host == currentRequest.host },
            clear = { token = null },
        )
        assertTrue(currentCleared)
        assertNull(token)
    }

    @Test
    fun latestPairingAttemptWinsEvenWhenOlderResponseReturnsFirst() {
        val processState = SyncPairingProcessState()
        val gate = SyncPairingStateGate(processState)
        var installed = "old-token"
        val first = gate.beginPairingSnapshot { revision, endpointRevision, attempt ->
            request("first.desktop", null, revision, endpointRevision, attempt)
        }
        val second = gate.beginPairingSnapshot { revision, endpointRevision, attempt ->
            request("second.desktop", null, revision, endpointRevision, attempt)
        }

        assertFalse(
            gate.replacePairingIfCurrent(first, endpointChanged = true) {
                installed = "first-token"
            },
        )
        assertTrue(
            gate.replacePairingIfCurrent(second, endpointChanged = true) {
                installed = "second-token"
            },
        )
        assertEquals("second-token", installed)
    }

    @Test
    fun pairingIntentInvalidatesOldAuthResponseBeforeFreshTokenCommit() {
        val processState = SyncPairingProcessState()
        val gate = SyncPairingStateGate(processState)
        var token: String? = "rejected-token"
        val authRequest = gate.snapshot { revision, endpointRevision ->
            request("desktop.local", token, revision, endpointRevision)
        }
        val pairingRequest = gate.beginPairingSnapshot { revision, endpointRevision, attempt ->
            request("desktop.local", null, revision, endpointRevision, attempt)
        }

        assertFalse(
            gate.clearRejectedIfCurrent(
                expected = authRequest,
                currentStoreMatches = { true },
                clear = { token = null },
            ),
        )
        assertEquals("rejected-token", token)
        assertTrue(
            gate.replacePairingIfCurrent(pairingRequest, endpointChanged = false) {
                token = "fresh-token"
            },
        )
        assertEquals("fresh-token", token)
    }

    @Test
    fun activePairingBlocksNewAuthAndInvalidatesOldCycleSideEffects() {
        val processState = SyncPairingProcessState()
        val gate = SyncPairingStateGate(processState)
        var oldEffects = 0
        val oldRequest = gate.authenticatedSnapshot { revision, endpointRevision ->
            request("desktop.local", "old-token", revision, endpointRevision)
        }
        val pairingRequest = gate.beginPairingSnapshot { revision, endpointRevision, attempt ->
            request("desktop.local", null, revision, endpointRevision, attempt)
        }

        assertFalse(
            gate.runIfCurrent(oldRequest, currentStoreMatches = { true }) {
                oldEffects += 1
            },
        )
        assertEquals(0, oldEffects)
        try {
            gate.authenticatedSnapshot { revision, endpointRevision ->
                request("desktop.local", "old-token", revision, endpointRevision)
            }
            fail("authenticated work must wait until pairing finishes")
        } catch (_: SyncPairingChangedException) {
            // Expected.
        }

        assertTrue(gate.finishPairingIfCurrent(pairingRequest))
        val resumed = gate.authenticatedSnapshot { revision, endpointRevision ->
            request("desktop.local", "old-token", revision, endpointRevision)
        }
        assertEquals("old-token", resumed.bearerToken)
    }

    @Test
    fun onlyLatestPairingAttemptCanReleaseAuthenticatedWork() {
        val processState = SyncPairingProcessState()
        val gate = SyncPairingStateGate(processState)
        val first = gate.beginPairingSnapshot { revision, endpointRevision, attempt ->
            request("desktop.local", null, revision, endpointRevision, attempt)
        }
        val second = gate.beginPairingSnapshot { revision, endpointRevision, attempt ->
            request("desktop.local", null, revision, endpointRevision, attempt)
        }

        assertFalse(gate.finishPairingIfCurrent(first))
        try {
            gate.authenticatedSnapshot { revision, endpointRevision ->
                request("desktop.local", "old-token", revision, endpointRevision)
            }
            fail("older request must not release a newer pairing attempt")
        } catch (_: SyncPairingChangedException) {
            // Expected.
        }
        assertTrue(gate.finishPairingIfCurrent(second))
        assertFalse(gate.finishPairingIfCurrent(second))
    }

    @Test
    fun snapshotFailureReleasesOnlyItsOwnActivePairing() {
        val processState = SyncPairingProcessState()
        val gate = SyncPairingStateGate(processState)

        try {
            gate.beginPairingSnapshot { _, _, _ ->
                throw IllegalStateException("simulated local snapshot failure")
            }
            fail("snapshot failure must propagate")
        } catch (_: IllegalStateException) {
            // Expected.
        }

        val resumed = gate.authenticatedSnapshot { revision, endpointRevision ->
            request("desktop.local", "old-token", revision, endpointRevision)
        }
        assertEquals("old-token", resumed.bearerToken)
    }

    private fun request(
        host: String,
        token: String?,
        revision: Long,
        endpointRevision: Long,
        pairingAttempt: Long? = null,
    ): SyncRequestSnapshot = SyncRequestSnapshot(
        host = host,
        port = 8787,
        bearerToken = token,
        revision = revision,
        endpointRevision = endpointRevision,
        pairingAttempt = pairingAttempt,
    )
}
