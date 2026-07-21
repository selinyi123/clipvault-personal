package com.clipvault.app.ime

/** Opaque generation for one Android input session. */
internal data class ImeCandidateInputSessionToken(
    val generation: Long,
)

/**
 * Opaque generation for the latest candidate request in an input session.
 *
 * Keeping the session token in the request token prevents a completed request
 * from becoming valid again after Android moves the IME to another editor.
 */
internal data class ImeCandidateRequestToken(
    val inputSession: ImeCandidateInputSessionToken,
    val generation: Long,
)

/**
 * Thread-safe latest-request gate for asynchronous IME candidate reads.
 *
 * The gate stores generations only. It never retains an EditorInfo,
 * InputConnection, Context, candidate payload, or ordinary typed text.
 */
internal class ImeCandidateRequestGate {
    private val lock = Any()
    private var inputGeneration = 0L
    private var requestGeneration = 0L
    private var currentInput: ImeCandidateInputSessionToken? = null
    private var currentRequest: ImeCandidateRequestToken? = null
    private var destroyed = false

    /** Starts a new editor generation and invalidates every older request. */
    fun beginInput(): ImeCandidateInputSessionToken = synchronized(lock) {
        inputGeneration = nextGeneration(inputGeneration)
        val token = ImeCandidateInputSessionToken(inputGeneration)
        currentInput = if (destroyed) null else token
        currentRequest = null
        token
    }

    /**
     * Starts the latest request for [inputSession]. A stale or ended session is
     * rejected instead of being revived.
     */
    fun beginRequest(
        inputSession: ImeCandidateInputSessionToken,
    ): ImeCandidateRequestToken? = synchronized(lock) {
        if (destroyed || currentInput != inputSession) return@synchronized null
        requestGeneration = nextGeneration(requestGeneration)
        ImeCandidateRequestToken(inputSession, requestGeneration).also {
            currentRequest = it
        }
    }

    /** True only for the newest request in the active input session. */
    fun isCurrent(token: ImeCandidateRequestToken): Boolean = synchronized(lock) {
        !destroyed && currentInput == token.inputSession && currentRequest == token
    }

    /** Ends the editor generation and invalidates queued, posted, and rendered work. */
    fun endInput() = synchronized(lock) {
        currentInput = null
        currentRequest = null
    }

    /** Permanently fails closed for the rest of this service instance. */
    fun destroy() = synchronized(lock) {
        destroyed = true
        currentInput = null
        currentRequest = null
    }

    private fun nextGeneration(current: Long): Long =
        if (current == Long.MAX_VALUE) 1L else current + 1L
}
