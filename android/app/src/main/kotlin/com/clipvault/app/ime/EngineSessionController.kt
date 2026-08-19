package com.clipvault.app.ime

import com.clipvault.ime.engine.EngineCandidate
import com.clipvault.ime.engine.EngineInputContext
import com.clipvault.ime.engine.EngineKeyEvent
import com.clipvault.ime.engine.EngineState
import com.clipvault.ime.engine.EngineTransition
import com.clipvault.ime.engine.InputEngineAdapterV2
import com.clipvault.ime.engine.PageDirection
import java.util.UUID

internal enum class EngineEditorResult {
    APPLIED,
    REJECTED,
    AMBIGUOUS,
}

/** Android-free seam around InputConnection so session behavior is host-testable. */
internal interface EngineEditor {
    fun apply(previous: EngineState, transition: EngineTransition): EngineEditorResult
    fun commitDirect(text: String): EngineEditorResult
    fun deleteDirect(): EngineEditorResult
    fun clearComposition(): EngineEditorResult
}

internal data class EngineUiState(
    val candidates: List<EngineCandidate>,
    val composing: Boolean,
    val engineAvailable: Boolean,
)

/**
 * Owns one Engine Protocol V2 session for the current Android editor.
 *
 * It never retains EditorInfo, InputConnection, surrounding text, or committed
 * text. If an engine call fails before any editor effect, direct input remains
 * available. If a composition was live or an editor effect was ambiguous, the
 * session is retired without replaying the key.
 */
internal class EngineSessionController(
    private val engineFactory: () -> InputEngineAdapterV2,
    private val editor: EngineEditor,
    private val render: (EngineUiState) -> Unit,
) {
    private var engine: InputEngineAdapterV2? = null
    private var sessionId: String? = null
    private var state: EngineState = EngineState.empty()
    private var nextRequestSequence = 0L

    fun begin(context: EngineInputContext) {
        finish()
        val newEngine = try {
            engineFactory()
        } catch (_: RuntimeException) {
            renderUnavailable()
            return
        } catch (_: UnsatisfiedLinkError) {
            renderUnavailable()
            return
        }
        val newSessionId = "android-${UUID.randomUUID()}"
        try {
            val started = newEngine.startSession(newSessionId, 1, context)
            require(started.requestSequence == 1L)
            require(started.state.revision == 0L)
            engine = newEngine
            sessionId = newSessionId
            state = started.state
            nextRequestSequence = 2L
            renderCurrent()
        } catch (_: RuntimeException) {
            retire(newEngine, newSessionId, 2L, clearComposition = false)
            renderUnavailable()
        }
    }

    fun inputText(text: String): Boolean =
        mutate(
            call = { activeEngine, activeSession ->
                activeEngine.processKey(
                    sessionId = activeSession,
                    requestSequence = nextRequestSequence,
                    expectedRevision = state.revision,
                    event = EngineKeyEvent.text(text),
                )
            },
            directFallback = { editor.commitDirect(text) },
        )

    fun backspace(): Boolean =
        mutate(
            call = { activeEngine, activeSession ->
                activeEngine.processKey(
                    sessionId = activeSession,
                    requestSequence = nextRequestSequence,
                    expectedRevision = state.revision,
                    event = EngineKeyEvent.backspace(),
                )
            },
            directFallback = editor::deleteDirect,
        )

    fun selectCandidate(candidateId: String): Boolean =
        mutate(
            call = { activeEngine, activeSession ->
                activeEngine.selectCandidate(
                    sessionId = activeSession,
                    requestSequence = nextRequestSequence,
                    expectedRevision = state.revision,
                    candidateId = candidateId,
                )
            },
            directFallback = null,
        )

    fun commitComposition(): Boolean {
        if (state.preedit.isEmpty()) return true
        return mutate(
            call = { activeEngine, activeSession ->
                activeEngine.commitComposition(
                    sessionId = activeSession,
                    requestSequence = nextRequestSequence,
                    expectedRevision = state.revision,
                )
            },
            directFallback = null,
        )
    }

    fun cancelComposition(): Boolean {
        if (state.preedit.isEmpty()) return true
        return mutate(
            call = { activeEngine, activeSession ->
                activeEngine.cancelComposition(
                    sessionId = activeSession,
                    requestSequence = nextRequestSequence,
                    expectedRevision = state.revision,
                )
            },
            directFallback = null,
        )
    }

    fun pageCandidates(direction: PageDirection): Boolean =
        mutate(
            call = { activeEngine, activeSession ->
                activeEngine.pageCandidates(
                    sessionId = activeSession,
                    requestSequence = nextRequestSequence,
                    expectedRevision = state.revision,
                    direction = direction,
                )
            },
            directFallback = null,
        )

    fun finish() {
        val activeEngine = engine
        val activeSession = sessionId
        val terminalSequence = nextRequestSequence
        val hadComposition = state.preedit.isNotEmpty()
        wipeLocal()
        if (hadComposition) editor.clearComposition()
        if (activeEngine != null && activeSession != null && terminalSequence >= 2) {
            try {
                activeEngine.endSession(activeSession, terminalSequence)
            } catch (_: RuntimeException) {
                // Local retirement is authoritative; no key or text is replayed.
            }
        }
        renderUnavailable()
    }

    private fun mutate(
        call: (InputEngineAdapterV2, String) -> EngineTransition,
        directFallback: (() -> EngineEditorResult)?,
    ): Boolean {
        val activeEngine = engine
        val activeSession = sessionId
        if (activeEngine == null || activeSession == null) {
            return directFallback?.invoke() == EngineEditorResult.APPLIED
        }

        val previous = state
        val transition = try {
            call(activeEngine, activeSession)
        } catch (_: RuntimeException) {
            val mayFallback = previous.preedit.isEmpty()
            retire(
                activeEngine,
                activeSession,
                nextRequestSequence,
                clearComposition = !mayFallback,
            )
            renderUnavailable()
            return mayFallback && directFallback?.invoke() == EngineEditorResult.APPLIED
        }

        if (!validSuccessor(previous, transition)) {
            retire(
                activeEngine,
                activeSession,
                nextRequestSequence + 1,
                clearComposition = previous.preedit.isNotEmpty(),
            )
            renderUnavailable()
            return false
        }

        return when (editor.apply(previous, transition)) {
            EngineEditorResult.APPLIED -> {
                state = transition.state
                nextRequestSequence += 1
                renderCurrent()
                true
            }
            EngineEditorResult.REJECTED,
            EngineEditorResult.AMBIGUOUS -> {
                retire(activeEngine, activeSession, nextRequestSequence + 1, clearComposition = false)
                renderUnavailable()
                false
            }
        }
    }

    private fun validSuccessor(previous: EngineState, transition: EngineTransition): Boolean =
        transition.requestSequence == nextRequestSequence &&
            transition.state.revision == previous.revision + 1

    private fun retire(
        retiringEngine: InputEngineAdapterV2,
        retiringSession: String,
        terminalSequence: Long,
        clearComposition: Boolean,
    ) {
        wipeLocal()
        if (clearComposition) editor.clearComposition()
        try {
            retiringEngine.endSession(retiringSession, terminalSequence)
        } catch (_: RuntimeException) {
            // The engine may already have invalidated the session.
        }
    }

    private fun wipeLocal() {
        engine = null
        sessionId = null
        state = EngineState.empty()
        nextRequestSequence = 0
    }

    private fun renderCurrent() {
        render(
            EngineUiState(
                candidates = state.candidates,
                composing = state.preedit.isNotEmpty(),
                engineAvailable = true,
            ),
        )
    }

    private fun renderUnavailable() {
        render(EngineUiState(emptyList(), composing = false, engineAvailable = false))
    }
}
