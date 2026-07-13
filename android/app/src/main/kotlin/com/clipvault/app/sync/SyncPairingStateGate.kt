package com.clipvault.app.sync

/** Immutable endpoint/authentication state captured before network I/O.
 *
 * This is deliberately not a data class: its default string representation
 * must never include the bearer token.
 */
internal class SyncRequestSnapshot(
    val host: String,
    val port: Int,
    val bearerToken: String?,
    internal val revision: Long,
    internal val endpointRevision: Long,
    internal val pairingAttempt: Long?,
) {
    val baseUrl: String
        get() = "http://$host:$port/api"
}

/** Process-wide pairing state shared by every Settings instance. Android's
 * current application topology is single-process; a source-boundary test
 * guards that assumption. Requests only hold this monitor while copying the
 * endpoint/token state, never while doing network I/O.
 */
internal class SyncPairingProcessState {
    internal val monitor = Any()
    internal var revision: Long = 0L
    internal var endpointRevision: Long = 0L
    internal var pairingAttempt: Long = 0L
}

private val DEFAULT_SYNC_PAIRING_PROCESS_STATE = SyncPairingProcessState()

internal class SyncPairingStateGate(
    private val state: SyncPairingProcessState = DEFAULT_SYNC_PAIRING_PROCESS_STATE,
) {
    fun <T> read(block: () -> T): T = synchronized(state.monitor) { block() }

    fun snapshot(
        read: (revision: Long, endpointRevision: Long) -> SyncRequestSnapshot,
    ): SyncRequestSnapshot = synchronized(state.monitor) {
        read(state.revision, state.endpointRevision)
    }

    /** Register user pairing intent before the one-time-code request starts.
     * A later attempt supersedes an earlier slow response even before either
     * response mutates stored endpoint/token state.
     */
    fun beginPairingSnapshot(
        read: (revision: Long, endpointRevision: Long, pairingAttempt: Long) -> SyncRequestSnapshot,
    ): SyncRequestSnapshot = synchronized(state.monitor) {
        state.pairingAttempt += 1L
        read(state.revision, state.endpointRevision, state.pairingAttempt)
    }

    /** Advance the revision before mutation. Even when persistence fails midway,
     * an already in-flight response can no longer mutate the new/partial state.
     */
    fun <T> replace(block: () -> T): T = synchronized(state.monitor) {
        state.revision += 1L
        state.pairingAttempt += 1L
        block()
    }

    fun <T> replaceEndpoint(block: () -> T): T = synchronized(state.monitor) {
        state.revision += 1L
        state.endpointRevision += 1L
        state.pairingAttempt += 1L
        block()
    }

    /** Apply a response-triggered mutation only if no pairing mutation has
     * happened since that request took its snapshot. The caller can add a
     * current-store predicate for defensive endpoint validation.
     */
    fun clearRejectedIfCurrent(
        expected: SyncRequestSnapshot,
        currentStoreMatches: () -> Boolean,
        clear: () -> Unit,
    ): Boolean = synchronized(state.monitor) {
        if (state.revision != expected.revision || !currentStoreMatches()) return@synchronized false
        // Invalidate response side effects from every other request using the
        // rejected token, but do not invalidate an explicit pairing attempt
        // that is concurrently redeeming a fresh one-time code.
        state.revision += 1L
        clear()
        true
    }

    fun replacePairingIfCurrent(
        expected: SyncRequestSnapshot,
        endpointChanged: Boolean,
        replace: () -> Unit,
    ): Boolean = synchronized(state.monitor) {
        val attempt = expected.pairingAttempt ?: return@synchronized false
        if (
            state.pairingAttempt != attempt ||
            state.endpointRevision != expected.endpointRevision
        ) return@synchronized false
        state.revision += 1L
        if (endpointChanged) state.endpointRevision += 1L
        // Consume the attempt so the same response cannot be committed twice.
        state.pairingAttempt += 1L
        replace()
        true
    }

    fun isCurrent(
        expected: SyncRequestSnapshot,
        currentStoreMatches: () -> Boolean,
    ): Boolean = synchronized(state.monitor) {
        state.revision == expected.revision && currentStoreMatches()
    }

    /** Recheck and linearize a response-dependent local side effect with
     * pairing replacement. Network I/O must never be placed in this block.
     */
    fun runIfCurrent(
        expected: SyncRequestSnapshot,
        currentStoreMatches: () -> Boolean,
        block: () -> Unit,
    ): Boolean = synchronized(state.monitor) {
        if (state.revision != expected.revision || !currentStoreMatches()) return@synchronized false
        block()
        true
    }
}
