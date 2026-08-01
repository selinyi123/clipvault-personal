package com.clipvault.ime.engine

/**
 * Production, platform-neutral Engine Protocol V2 boundary.
 *
 * Implementations must remain local to the device. They may keep composition
 * state only for the live session, but must not persist key events or committed
 * text and must not perform network work.
 */
interface InputEngineAdapterV2 {
    val hostEpoch: String

    fun startSession(
        sessionId: String,
        requestSequence: Long,
        context: EngineInputContext,
    ): EngineTransition

    fun processKey(
        sessionId: String,
        requestSequence: Long,
        expectedRevision: Long,
        event: EngineKeyEvent,
    ): EngineTransition

    fun selectCandidate(
        sessionId: String,
        requestSequence: Long,
        expectedRevision: Long,
        candidateId: String,
    ): EngineTransition

    fun pageCandidates(
        sessionId: String,
        requestSequence: Long,
        expectedRevision: Long,
        direction: PageDirection,
    ): EngineTransition

    fun cancelComposition(
        sessionId: String,
        requestSequence: Long,
        expectedRevision: Long,
    ): EngineTransition

    fun commitComposition(
        sessionId: String,
        requestSequence: Long,
        expectedRevision: Long,
    ): EngineTransition

    fun endSession(sessionId: String, requestSequence: Long)
}

enum class EngineFieldKind {
    TEXT,
    NUMBER,
    PHONE,
    EMAIL,
    URL,
    PASSWORD,
    UNKNOWN,
}

data class EngineInputContext(
    val fieldKind: EngineFieldKind,
    val incognito: Boolean,
    val learningAllowed: Boolean,
    val clipVaultAllowed: Boolean,
) {
    init {
        require(!learningAllowed || (!incognito && fieldKind != EngineFieldKind.PASSWORD))
        require(!clipVaultAllowed || (!incognito && fieldKind != EngineFieldKind.PASSWORD))
    }
}

enum class EngineKeyKind {
    TEXT,
    BACKSPACE,
}

data class EngineKeyEvent(
    val kind: EngineKeyKind,
    val text: String? = null,
) {
    init {
        when (kind) {
            EngineKeyKind.TEXT -> require(!text.isNullOrEmpty() && text.length <= MAX_KEY_TEXT_UTF16)
            EngineKeyKind.BACKSPACE -> require(text == null)
        }
    }

    companion object {
        const val MAX_KEY_TEXT_UTF16 = 8

        fun text(value: String) = EngineKeyEvent(EngineKeyKind.TEXT, value)
        fun backspace() = EngineKeyEvent(EngineKeyKind.BACKSPACE)
    }
}

enum class PageDirection { PREVIOUS, NEXT }
enum class EngineMode { DIRECT, COMPOSING }

data class EngineCandidate(
    val id: String,
    val text: String,
    val comment: String? = null,
) {
    init {
        require(id.matches(OPAQUE_ID))
        require(text.isNotEmpty() && text.length <= MAX_CANDIDATE_UTF16)
        require(comment == null || comment.length <= MAX_COMMENT_UTF16)
    }

    companion object {
        private val OPAQUE_ID = Regex("[A-Za-z0-9._:-]{1,128}")
        private const val MAX_CANDIDATE_UTF16 = 4096
        private const val MAX_COMMENT_UTF16 = 512
    }
}

data class EngineState(
    val revision: Long,
    val preedit: String,
    val caretUtf16: Int,
    val candidates: List<EngineCandidate>,
    val pageIndex: Int,
    val hasPreviousPage: Boolean,
    val hasNextPage: Boolean,
    val mode: EngineMode,
) {
    init {
        require(revision >= 0)
        require(preedit.length <= MAX_PREEDIT_UTF16)
        require(caretUtf16 in 0..preedit.length)
        require(!splitsSurrogatePair(preedit, caretUtf16))
        require(candidates.size <= MAX_VISIBLE_CANDIDATES)
        require(candidates.map { it.id }.toSet().size == candidates.size)
        require(pageIndex >= 0)
        if (preedit.isEmpty()) require(caretUtf16 == 0)
    }

    companion object {
        private const val MAX_PREEDIT_UTF16 = 4096
        private const val MAX_VISIBLE_CANDIDATES = 100

        fun empty(revision: Long = 0) = EngineState(
            revision = revision,
            preedit = "",
            caretUtf16 = 0,
            candidates = emptyList(),
            pageIndex = 0,
            hasPreviousPage = false,
            hasNextPage = false,
            mode = EngineMode.DIRECT,
        )

        private fun splitsSurrogatePair(value: String, offset: Int): Boolean =
            offset > 0 && offset < value.length &&
                value[offset - 1].isHighSurrogate() && value[offset].isLowSurrogate()
    }
}

data class EngineTransition(
    val requestSequence: Long,
    val state: EngineState,
    val commitText: String? = null,
    val deleteBeforeCodePoints: Int = 0,
) {
    init {
        require(requestSequence >= 1)
        require(commitText == null || (commitText.isNotEmpty() && commitText.length <= MAX_COMMIT_UTF16))
        require(deleteBeforeCodePoints in 0..MAX_DELETE_CODE_POINTS)
        require(commitText == null || deleteBeforeCodePoints == 0)
        if (commitText != null) {
            require(state.preedit.isEmpty())
            require(state.candidates.isEmpty())
        }
    }

    companion object {
        private const val MAX_COMMIT_UTF16 = 16_384
        private const val MAX_DELETE_CODE_POINTS = 64
    }
}

class EngineProtocolException(message: String) : IllegalStateException(message)
