package com.clipvault.app.ime.rime

import com.clipvault.ime.engine.EngineCandidate
import com.clipvault.ime.engine.EngineInputContext
import com.clipvault.ime.engine.EngineKeyEvent
import com.clipvault.ime.engine.EngineKeyKind
import com.clipvault.ime.engine.EngineMode
import com.clipvault.ime.engine.EngineProtocolException
import com.clipvault.ime.engine.EngineState
import com.clipvault.ime.engine.EngineTransition
import com.clipvault.ime.engine.InputEngineAdapterV2
import com.clipvault.ime.engine.PageDirection
import java.util.UUID

internal class NativeRimeInputEngineAdapter(
    private val api: RimeNativeApi,
) : InputEngineAdapterV2 {
    override val hostEpoch: String = UUID.randomUUID().toString()

    private var liveSessionId: String? = null
    private var nativeSession = 0L
    private var nextSequence = 0L
    private var revision = 0L
    private var candidateIndex = emptyMap<String, Int>()
    /**
     * Candidate IDs are transport identities, not an encoding of the current
     * page or array position. Keep the generated ID stable while Rime pages
     * the same composition, then discard it when the composition changes.
     */
    private val candidateIds = mutableMapOf<CandidateIdentity, String>()
    private var lastState = EngineState.empty()

    override fun startSession(
        sessionId: String,
        requestSequence: Long,
        context: EngineInputContext,
    ): EngineTransition {
        if (liveSessionId != null || requestSequence != 1L) {
            throw EngineProtocolException("invalid native StartSession")
        }
        nativeSession = api.createSession()
        if (nativeSession == 0L) throw EngineProtocolException("native session unavailable")
        candidateIds.clear()
        val schemaId = if (context.learningAllowed) NORMAL_SCHEMA else PRIVATE_SCHEMA
        if (!api.selectSchema(nativeSession, schemaId)) {
            api.destroySession(nativeSession)
            nativeSession = 0L
            throw EngineProtocolException("native schema unavailable")
        }
        if (context.fieldKind in ASCII_FIELD_KINDS) {
            api.setOption(nativeSession, "ascii_mode", true)
        }
        liveSessionId = sessionId
        nextSequence = 2L
        revision = 0L
        return try {
            transition(requestSequence, snapshotState())
        } catch (failure: RuntimeException) {
            destroyAndResetNativeSession()
            throw failure
        } catch (failure: LinkageError) {
            destroyAndResetNativeSession()
            throw failure
        }
    }

    override fun processKey(
        sessionId: String,
        requestSequence: Long,
        expectedRevision: Long,
        event: EngineKeyEvent,
    ): EngineTransition {
        checkRequest(sessionId, requestSequence, expectedRevision)
        val previous = lastState
        val handled = when (event.kind) {
            EngineKeyKind.TEXT -> {
                val text = requireNotNull(event.text)
                val codePoint = text.singleCodePointOrNull()
                codePoint != null && api.processKey(nativeSession, codePoint, 0)
            }
            EngineKeyKind.BACKSPACE -> api.processKey(nativeSession, XK_BACKSPACE, 0)
        }
        revision += 1
        nextSequence += 1
        val commit = api.takeCommit(nativeSession)
        if (!commit.isNullOrEmpty()) {
            candidateIndex = emptyMap()
            return transition(requestSequence, EngineState.empty(revision), commitText = commit)
        }
        if (!handled) {
            candidateIndex = emptyMap()
            if (previous.preedit.isNotEmpty()) {
                return when (event.kind) {
                    EngineKeyKind.TEXT -> {
                        val committed = if (api.commitComposition(nativeSession)) {
                            api.takeCommit(nativeSession)
                        } else {
                            null
                        }
                        if (committed.isNullOrEmpty()) api.clearComposition(nativeSession)
                        transition(
                            requestSequence,
                            EngineState.empty(revision),
                            commitText = (committed.orEmpty() + requireNotNull(event.text)),
                        )
                    }
                    EngineKeyKind.BACKSPACE -> {
                        api.clearComposition(nativeSession)
                        transition(requestSequence, EngineState.empty(revision))
                    }
                }
            }
            return when (event.kind) {
                EngineKeyKind.TEXT -> transition(
                    requestSequence,
                    EngineState.empty(revision),
                    commitText = requireNotNull(event.text),
                )
                EngineKeyKind.BACKSPACE -> transition(
                    requestSequence,
                    EngineState.empty(revision),
                    deleteBeforeCodePoints = 1,
                )
            }
        }
        return transition(requestSequence, snapshotState())
    }

    override fun selectCandidate(
        sessionId: String,
        requestSequence: Long,
        expectedRevision: Long,
        candidateId: String,
    ): EngineTransition {
        checkRequest(sessionId, requestSequence, expectedRevision)
        val index = candidateIndex[candidateId]
            ?: throw EngineProtocolException("stale native candidate")
        if (!api.selectCandidate(nativeSession, index)) {
            throw EngineProtocolException("native candidate selection failed")
        }
        revision += 1
        nextSequence += 1
        val commit = api.takeCommit(nativeSession)
        return if (!commit.isNullOrEmpty()) {
            candidateIndex = emptyMap()
            transition(requestSequence, EngineState.empty(revision), commitText = commit)
        } else {
            transition(requestSequence, snapshotState())
        }
    }

    override fun pageCandidates(
        sessionId: String,
        requestSequence: Long,
        expectedRevision: Long,
        direction: PageDirection,
    ): EngineTransition {
        checkRequest(sessionId, requestSequence, expectedRevision)
        val key = if (direction == PageDirection.NEXT) XK_PAGE_DOWN else XK_PAGE_UP
        if (!api.processKey(nativeSession, key, 0)) {
            throw EngineProtocolException("native candidate page unavailable")
        }
        revision += 1
        nextSequence += 1
        return transition(requestSequence, snapshotState())
    }

    override fun cancelComposition(
        sessionId: String,
        requestSequence: Long,
        expectedRevision: Long,
    ): EngineTransition {
        checkRequest(sessionId, requestSequence, expectedRevision)
        api.clearComposition(nativeSession)
        revision += 1
        nextSequence += 1
        candidateIndex = emptyMap()
        return transition(requestSequence, EngineState.empty(revision))
    }

    override fun commitComposition(
        sessionId: String,
        requestSequence: Long,
        expectedRevision: Long,
    ): EngineTransition {
        checkRequest(sessionId, requestSequence, expectedRevision)
        val hadComposition = lastState.preedit.isNotEmpty()
        val accepted = !hadComposition || api.commitComposition(nativeSession)
        revision += 1
        nextSequence += 1
        if (!accepted) {
            api.clearComposition(nativeSession)
            candidateIndex = emptyMap()
            return transition(requestSequence, EngineState.empty(revision))
        }
        val commit = if (hadComposition) api.takeCommit(nativeSession) else null
        candidateIndex = emptyMap()
        return if (commit.isNullOrEmpty()) {
            transition(requestSequence, EngineState.empty(revision))
        } else {
            transition(requestSequence, EngineState.empty(revision), commitText = commit)
        }
    }

    override fun endSession(sessionId: String, requestSequence: Long) {
        requireLive(sessionId)
        if (requestSequence != nextSequence) throw EngineProtocolException("native end sequence mismatch")
        api.destroySession(nativeSession)
        resetNativeSession()
    }

    private fun transition(
        requestSequence: Long,
        state: EngineState,
        commitText: String? = null,
        deleteBeforeCodePoints: Int = 0,
    ): EngineTransition = EngineTransition(
        requestSequence = requestSequence,
        state = state,
        commitText = commitText,
        deleteBeforeCodePoints = deleteBeforeCodePoints,
    ).also { lastState = state }

    private fun snapshotState(): EngineState {
        val raw = api.snapshot(nativeSession)
        if (raw.size < 4 || (raw.size - 4) % 2 != 0) {
            throw EngineProtocolException("invalid native snapshot")
        }
        val preedit = raw[0]
        val caret = raw[1].toIntOrNull()
            ?: throw EngineProtocolException("invalid native caret")
        val page = raw[2].toIntOrNull()
            ?: throw EngineProtocolException("invalid native page")
        if (caret !in 0..preedit.length ||
            (caret > 0 && caret < preedit.length &&
                preedit[caret - 1].isHighSurrogate() && preedit[caret].isLowSurrogate())
        ) {
            throw EngineProtocolException("native caret is outside UTF-16 boundaries")
        }
        if (page < 0 || raw[3] !in setOf("0", "1")) {
            throw EngineProtocolException("invalid native page metadata")
        }
        val lastPage = raw[3] == "1"
        if (preedit != lastState.preedit) candidateIds.clear()
        val occurrences = mutableMapOf<CandidateText, Int>()
        val candidates = raw.drop(4).chunked(2).map { pair ->
            val text = pair[0]
            val comment = pair[1].ifEmpty { null }
            val textKey = CandidateText(text, comment)
            val occurrence = occurrences.getOrDefault(textKey, 0)
            occurrences[textKey] = occurrence + 1
            val identity = CandidateIdentity(textKey, occurrence)
            val candidateId = candidateIds.getOrPut(identity) {
                "rime:${UUID.randomUUID()}"
            }
            EngineCandidate(
                id = candidateId,
                text = text,
                comment = comment,
            )
        }
        candidateIndex = candidates.mapIndexed { index, candidate -> candidate.id to index }.toMap()
        return EngineState(
            revision = revision,
            preedit = preedit,
            caretUtf16 = caret,
            candidates = candidates,
            pageIndex = page,
            hasPreviousPage = page > 0,
            hasNextPage = candidates.isNotEmpty() && !lastPage,
            mode = if (preedit.isEmpty()) EngineMode.DIRECT else EngineMode.COMPOSING,
        )
    }

    private fun checkRequest(sessionId: String, requestSequence: Long, expectedRevision: Long) {
        requireLive(sessionId)
        if (requestSequence != nextSequence || expectedRevision != revision) {
            throw EngineProtocolException("native sequence or revision mismatch")
        }
    }

    private fun requireLive(sessionId: String) {
        if (liveSessionId != sessionId || nativeSession == 0L) {
            throw EngineProtocolException("unknown native session")
        }
    }

    private fun resetNativeSession() {
        liveSessionId = null
        nativeSession = 0
        nextSequence = 0
        revision = 0
        candidateIndex = emptyMap()
        candidateIds.clear()
        lastState = EngineState.empty()
    }

    private fun destroyAndResetNativeSession() {
        val session = nativeSession
        if (session != 0L) {
            runCatching { api.destroySession(session) }
        }
        resetNativeSession()
    }

    private fun String.singleCodePointOrNull(): Int? {
        val count = codePointCount(0, length)
        return if (count == 1) codePointAt(0) else null
    }

    private data class CandidateText(val text: String, val comment: String?)

    private data class CandidateIdentity(
        val text: CandidateText,
        val occurrence: Int,
    )

    private companion object {
        const val NORMAL_SCHEMA = "clipvault_pinyin"
        const val PRIVATE_SCHEMA = "clipvault_pinyin_private"
        const val XK_BACKSPACE = 0xff08
        const val XK_PAGE_UP = 0xff55
        const val XK_PAGE_DOWN = 0xff56
        val ASCII_FIELD_KINDS = setOf(
            com.clipvault.ime.engine.EngineFieldKind.NUMBER,
            com.clipvault.ime.engine.EngineFieldKind.PHONE,
            com.clipvault.ime.engine.EngineFieldKind.EMAIL,
            com.clipvault.ime.engine.EngineFieldKind.URL,
        )
    }
}
