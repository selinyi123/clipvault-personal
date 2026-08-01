package com.clipvault.ime.engine

import java.util.UUID

/**
 * Local no-dictionary fallback used while the native decoder is unavailable.
 * It deliberately retains no committed text: every printable key is returned
 * immediately as a one-shot commit operation.
 */
class DirectInputEngineAdapter : InputEngineAdapterV2 {
    override val hostEpoch: String = UUID.randomUUID().toString()

    private var liveSessionId: String? = null
    private var nextSequence = 0L
    private var revision = 0L

    override fun startSession(
        sessionId: String,
        requestSequence: Long,
        context: EngineInputContext,
    ): EngineTransition {
        requireSessionId(sessionId)
        if (liveSessionId != null) throw EngineProtocolException("a session is already active")
        if (requestSequence != 1L) throw EngineProtocolException("StartSession must use sequence 1")
        liveSessionId = sessionId
        nextSequence = 2L
        revision = 0L
        return EngineTransition(requestSequence, EngineState.empty())
    }

    override fun processKey(
        sessionId: String,
        requestSequence: Long,
        expectedRevision: Long,
        event: EngineKeyEvent,
    ): EngineTransition {
        checkRequest(sessionId, requestSequence, expectedRevision)
        revision += 1
        nextSequence += 1
        return when (event.kind) {
            EngineKeyKind.TEXT -> EngineTransition(
                requestSequence = requestSequence,
                state = EngineState.empty(revision),
                commitText = requireNotNull(event.text),
            )
            EngineKeyKind.BACKSPACE -> EngineTransition(
                requestSequence = requestSequence,
                state = EngineState.empty(revision),
                deleteBeforeCodePoints = 1,
            )
        }
    }

    override fun selectCandidate(
        sessionId: String,
        requestSequence: Long,
        expectedRevision: Long,
        candidateId: String,
    ): EngineTransition {
        checkRequest(sessionId, requestSequence, expectedRevision)
        throw EngineProtocolException("direct mode has no candidate $candidateId")
    }

    override fun pageCandidates(
        sessionId: String,
        requestSequence: Long,
        expectedRevision: Long,
        direction: PageDirection,
    ): EngineTransition {
        checkRequest(sessionId, requestSequence, expectedRevision)
        throw EngineProtocolException("direct mode has no candidate pages")
    }

    override fun cancelComposition(
        sessionId: String,
        requestSequence: Long,
        expectedRevision: Long,
    ): EngineTransition {
        checkRequest(sessionId, requestSequence, expectedRevision)
        revision += 1
        nextSequence += 1
        return EngineTransition(requestSequence, EngineState.empty(revision))
    }

    override fun commitComposition(
        sessionId: String,
        requestSequence: Long,
        expectedRevision: Long,
    ): EngineTransition {
        checkRequest(sessionId, requestSequence, expectedRevision)
        revision += 1
        nextSequence += 1
        return EngineTransition(requestSequence, EngineState.empty(revision))
    }

    override fun endSession(sessionId: String, requestSequence: Long) {
        requireLiveSession(sessionId)
        if (requestSequence != nextSequence) {
            throw EngineProtocolException("EndSession sequence mismatch")
        }
        wipeSession()
    }

    private fun checkRequest(sessionId: String, requestSequence: Long, expectedRevision: Long) {
        requireLiveSession(sessionId)
        if (requestSequence != nextSequence) throw EngineProtocolException("request sequence mismatch")
        if (expectedRevision != revision) throw EngineProtocolException("state revision mismatch")
    }

    private fun requireLiveSession(sessionId: String) {
        if (liveSessionId != sessionId) throw EngineProtocolException("unknown or stale session")
    }

    private fun requireSessionId(sessionId: String) {
        if (!sessionId.matches(SESSION_ID)) throw EngineProtocolException("invalid session id")
    }

    private fun wipeSession() {
        liveSessionId = null
        nextSequence = 0
        revision = 0
    }

    companion object {
        private val SESSION_ID = Regex("[A-Za-z0-9._:-]{1,128}")
    }
}
