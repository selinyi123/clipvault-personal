package com.clipvault.imeapp

import android.inputmethodservice.InputMethodService
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.text.InputType
import android.util.Size
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import android.view.HapticFeedbackConstants
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InlineSuggestionsRequest
import android.view.inputmethod.InlineSuggestionsResponse
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.inline.InlinePresentationSpec
import com.clipvault.app.ime.rime.RimeEngineFactory
import com.clipvault.app.ime.rime.RimeReadiness
import com.clipvault.ime.engine.DirectInputEngineAdapter
import com.clipvault.ime.engine.EngineCandidate
import com.clipvault.ime.engine.EngineFieldKind
import com.clipvault.ime.engine.EngineInputContext
import com.clipvault.ime.engine.EngineKeyEvent
import com.clipvault.ime.engine.EngineKeyKind
import com.clipvault.ime.engine.EngineState
import com.clipvault.ime.engine.EngineTransition
import com.clipvault.ime.engine.InputEngineAdapterV2
import com.clipvault.ime.engine.PageDirection
import java.util.UUID

/** Standalone no-network IME shell with optional signature-protected snapshots. */
class ClipVaultIsolatedImeService : InputMethodService() {
    private enum class ShiftState { OFF, ONCE, LOCKED }
    private enum class LayoutMode { LETTERS, SYMBOLS }
    private enum class MutationKind { INPUT, BACKSPACE, SELECT, PAGE, COMMIT, CANCEL }

    private var engine: InputEngineAdapterV2? = null
    private var sessionId: String? = null
    private var state = EngineState.empty()
    private var nextSequence = 0L
    private var activeContext: EngineInputContext? = null
    private val mainHandler = Handler(Looper.getMainLooper())
    private val compositionKeys = StringBuilder()
    private var waitingForRime = false
    private var waitingStartedAt = 0L
    private lateinit var sensitiveAppPolicy: SensitiveAppPolicy

    private var inlineGeneration = 0L
    private var inputGeneration = 0L
    private var inlineHost: LinearLayout? = null
    private var candidateHost: LinearLayout? = null
    private var runtimeHost: LinearLayout? = null
    private var runtimeScroll: HorizontalScrollView? = null
    private var keyboardHost: LinearLayout? = null

    private var runtimeClient: RuntimeSnapshotClient? = null
    private var runtimeCandidates = emptyList<RuntimeCandidateSnapshot>()
    private var shiftState = ShiftState.OFF
    private var lastShiftTapAt = 0L
    private var layoutMode = LayoutMode.LETTERS
    private var forceEnglishMode = false
    private var editorAction = EditorInfo.IME_ACTION_NONE
    private val consumedHardwareKeys = mutableSetOf<Int>()
    private val backspaceRepeater by lazy {
        BackspaceRepeatController(
            scheduler = RepeatScheduler { delayMs, action ->
                val runnable = Runnable(action)
                mainHandler.postDelayed(runnable, delayMs)
                RepeatCancellation { mainHandler.removeCallbacks(runnable) }
            },
            deleteOnce = ::backspace,
        )
    }

    override fun onCreate() {
        super.onCreate()
        sensitiveAppPolicy = SensitiveAppPolicy { ImePreferences.readSensitivePackages(this) }
        runtimeClient = RuntimeSnapshotClient(this).also { client ->
            runCatching {
                client.bind(
                    onConnected = ::requestRuntimeSnapshotIfAllowed,
                    onInvalidated = ::clearRuntimeSurface,
                )
            }
        }
    }

    override fun onStartInput(attribute: EditorInfo?, restarting: Boolean) {
        super.onStartInput(attribute, restarting)
        backspaceRepeater.release()
        finishEngine(clearEditorComposition = true)
        inputGeneration += 1
        runtimeClient?.cancelPending()
        runtimeCandidates = emptyList()
        consumedHardwareKeys.clear()
        val context = inputContext(attribute)
        activeContext = context
        forceEnglishMode = false
        shiftState = ShiftState.OFF
        layoutMode = if (
            context.fieldKind == EngineFieldKind.NUMBER ||
            context.fieldKind == EngineFieldKind.PHONE
        ) LayoutMode.SYMBOLS else LayoutMode.LETTERS
        editorAction = (attribute?.imeOptions ?: 0) and EditorInfo.IME_MASK_ACTION
        startEngine(context)
        renderCandidates()
        renderRuntimeCandidates()
        renderKeyboard()
        requestRuntimeSnapshotIfAllowed()
    }

    override fun onFinishInput() {
        backspaceRepeater.release()
        commitComposition()
        inputGeneration += 1
        runtimeClient?.cancelPending()
        runtimeCandidates = emptyList()
        renderRuntimeCandidates()
        finishEngine(clearEditorComposition = false)
        activeContext = null
        super.onFinishInput()
    }

    override fun onDestroy() {
        inputGeneration += 1
        backspaceRepeater.release()
        mainHandler.removeCallbacksAndMessages(null)
        runtimeClient?.unbind()
        runtimeClient = null
        finishEngine(clearEditorComposition = true)
        super.onDestroy()
    }

    override fun onFinishInputView(finishingInput: Boolean) {
        backspaceRepeater.release()
        super.onFinishInputView(finishingInput)
    }

    override fun onCreateInputView(): View {
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        inlineHost = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            visibility = View.GONE
        }.also(root::addView)

        val candidateScroll = HorizontalScrollView(this)
        candidateHost = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        candidateScroll.addView(candidateHost)
        root.addView(candidateScroll)

        runtimeScroll = HorizontalScrollView(this).also { scroll ->
            runtimeHost = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
            scroll.addView(runtimeHost)
            root.addView(scroll)
        }

        keyboardHost = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }.also(root::addView)
        renderCandidates()
        renderRuntimeCandidates()
        renderKeyboard()
        return root
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        if (event.isCtrlPressed || event.isAltPressed || event.isMetaPressed) {
            return super.onKeyDown(keyCode, event)
        }
        val consumed = when (keyCode) {
            KeyEvent.KEYCODE_DEL -> backspace()
            KeyEvent.KEYCODE_ENTER,
            KeyEvent.KEYCODE_NUMPAD_ENTER,
            -> enter()
            KeyEvent.KEYCODE_ESCAPE -> if (waitingForRime || state.preedit.isNotEmpty()) {
                cancelComposition()
            } else {
                false
            }
            KeyEvent.KEYCODE_PAGE_UP -> if (state.hasPreviousPage) {
                page(PageDirection.PREVIOUS)
            } else {
                false
            }
            KeyEvent.KEYCODE_PAGE_DOWN -> if (state.hasNextPage) {
                page(PageDirection.NEXT)
            } else {
                false
            }
            else -> {
                val unicode = event.unicodeChar
                if (unicode > 0 && !Character.isISOControl(unicode)) {
                    input(String(Character.toChars(unicode)))
                } else {
                    false
                }
            }
        }
        if (consumed) consumedHardwareKeys += keyCode
        return consumed || super.onKeyDown(keyCode, event)
    }

    override fun onKeyUp(keyCode: Int, event: KeyEvent): Boolean {
        if (consumedHardwareKeys.remove(keyCode)) return true
        return super.onKeyUp(keyCode, event)
    }

    override fun onCreateInlineSuggestionsRequest(uiExtras: Bundle): InlineSuggestionsRequest {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
            throw UnsupportedOperationException("inline suggestions require API 30")
        }
        val spec = InlinePresentationSpec.Builder(
            Size(dp(48), dp(36)),
            Size(dp(320), dp(52)),
        ).build()
        return InlineSuggestionsRequest.Builder(listOf(spec))
            .setMaxSuggestionCount(3)
            .setExtras(uiExtras)
            .build()
    }

    override fun onInlineSuggestionsResponse(response: InlineSuggestionsResponse): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return false
        val host = inlineHost ?: return false
        val generation = ++inlineGeneration
        if (response.inlineSuggestions.isEmpty()) {
            host.removeAllViews()
            host.visibility = View.GONE
            return false
        }
        val views = arrayOfNulls<View>(response.inlineSuggestions.size)
        var completed = 0
        response.inlineSuggestions.forEachIndexed { index, suggestion ->
            // The IME inflates the protected system presentation only. It never
            // reads, reflects, serializes, or relays Autofill plaintext.
            suggestion.inflate(this, Size(dp(320), dp(48)), mainExecutor) { view ->
                if (generation != inlineGeneration) return@inflate
                views[index] = view
                completed += 1
                if (completed == views.size) {
                    host.removeAllViews()
                    views.filterNotNull().forEach(host::addView)
                    host.visibility = if (host.childCount == 0) View.GONE else View.VISIBLE
                }
            }
        }
        return true
    }

    private fun startEngine(context: EngineInputContext) {
        compositionKeys.clear()
        waitingForRime = false
        RimeEngineFactory.prewarmAsync(this)
        if (context.fieldKind == EngineFieldKind.PASSWORD || forceEnglishMode) {
            installDirect(context)
            return
        }
        when (RimeEngineFactory.readiness()) {
            RimeReadiness.READY -> if (!installNative(context)) installDirect(context)
            RimeReadiness.FAILED -> installDirect(context)
            RimeReadiness.IDLE,
            RimeReadiness.WARMING,
            -> awaitRime(context)
        }
    }

    private fun installNative(context: EngineInputContext): Boolean = try {
        beginEngineSession(RimeEngineFactory.create(this, context), context)
    } catch (_: RuntimeException) {
        false
    } catch (_: LinkageError) {
        false
    }

    private fun installDirect(context: EngineInputContext): Boolean =
        beginEngineSession(DirectInputEngineAdapter(), context)

    private fun beginEngineSession(
        selected: InputEngineAdapterV2,
        context: EngineInputContext,
    ): Boolean {
        val id = "isolated-${UUID.randomUUID()}"
        val started = try {
            selected.startSession(id, 1, context)
        } catch (_: RuntimeException) {
            return false
        } catch (_: LinkageError) {
            return false
        }
        engine = selected
        sessionId = id
        state = started.state
        nextSequence = 2
        return true
    }

    private fun awaitRime(context: EngineInputContext) {
        waitingForRime = true
        waitingStartedAt = SystemClock.elapsedRealtime()
        val generation = inputGeneration
        fun checkReadiness() {
            if (generation != inputGeneration || activeContext != context || !waitingForRime) return
            when (RimeEngineFactory.readiness()) {
                RimeReadiness.READY -> {
                    val pending = compositionKeys.toString()
                    compositionKeys.clear()
                    waitingForRime = false
                    if (installNative(context)) {
                        pending.codePoints().forEach { input(String(Character.toChars(it))) }
                    } else {
                        commitWaitingRawAndInstallDirect(context, pending)
                    }
                    renderCandidates()
                }
                RimeReadiness.FAILED -> commitWaitingRawAndInstallDirect(
                    context,
                    compositionKeys.toString(),
                )
                RimeReadiness.IDLE,
                RimeReadiness.WARMING,
                -> if (SystemClock.elapsedRealtime() - waitingStartedAt >= RIME_WAIT_TIMEOUT_MS) {
                    commitWaitingRawAndInstallDirect(context, compositionKeys.toString())
                } else {
                    mainHandler.postDelayed(::checkReadiness, RIME_READY_POLL_MS)
                }
            }
        }
        mainHandler.post(::checkReadiness)
    }

    private fun bufferWhileWaiting(event: EngineKeyEvent): Boolean {
        when (event.kind) {
            EngineKeyKind.TEXT -> compositionKeys.append(requireNotNull(event.text))
            EngineKeyKind.BACKSPACE -> compositionKeys.dropLastCodePoint()
        }
        val connection = currentInputConnection ?: return false
        val applied = if (compositionKeys.isEmpty()) {
            connection.setComposingText("", 1) && connection.finishComposingText()
        } else {
            connection.setComposingText(compositionKeys.toString(), 1)
        }
        renderCandidates()
        return applied
    }

    private fun commitWaitingRawAndInstallDirect(
        context: EngineInputContext,
        rawInput: String,
    ) {
        waitingForRime = false
        installDirect(context)
        if (rawInput.isNotEmpty()) {
            runCatching { currentInputConnection?.commitText(rawInput, 1) }
        } else {
            clearEditorComposition()
        }
        compositionKeys.clear()
        renderCandidates()
    }

    private fun input(text: String): Boolean {
        val event = EngineKeyEvent.text(text)
        if (waitingForRime) return bufferWhileWaiting(event)
        return mutate(MutationKind.INPUT, event) { activeEngine, id ->
            activeEngine.processKey(id, nextSequence, state.revision, event)
        }
    }

    private fun backspace(): Boolean {
        val event = EngineKeyEvent.backspace()
        if (waitingForRime) return bufferWhileWaiting(event)
        return mutate(MutationKind.BACKSPACE, event) { activeEngine, id ->
            activeEngine.processKey(id, nextSequence, state.revision, event)
        }
    }

    private fun select(candidate: EngineCandidate): Boolean = mutate(
        MutationKind.SELECT,
        recoveryCommitText = candidate.text,
    ) { activeEngine, id ->
        activeEngine.selectCandidate(id, nextSequence, state.revision, candidate.id)
    }

    private fun page(direction: PageDirection): Boolean = mutate(MutationKind.PAGE) { activeEngine, id ->
        activeEngine.pageCandidates(id, nextSequence, state.revision, direction)
    }

    private fun commitComposition(): Boolean {
        if (waitingForRime) {
            val context = activeContext ?: return false
            commitWaitingRawAndInstallDirect(context, compositionKeys.toString())
            return true
        }
        if (state.preedit.isEmpty()) return true
        return mutate(MutationKind.COMMIT) { activeEngine, id ->
            activeEngine.commitComposition(id, nextSequence, state.revision)
        }
    }

    private fun cancelComposition(): Boolean {
        if (waitingForRime) {
            waitingForRime = false
            compositionKeys.clear()
            clearEditorComposition()
            renderCandidates()
            return true
        }
        if (state.preedit.isEmpty()) return true
        return mutate(MutationKind.CANCEL) { activeEngine, id ->
            activeEngine.cancelComposition(id, nextSequence, state.revision)
        }
    }

    private fun mutate(
        kind: MutationKind,
        event: EngineKeyEvent? = null,
        recoveryCommitText: String? = null,
        call: (InputEngineAdapterV2, String) -> EngineTransition,
    ): Boolean {
        val activeEngine = engine ?: return false
        val activeId = sessionId ?: return false
        val previous = state
        val transition = try {
            call(activeEngine, activeId)
        } catch (_: RuntimeException) {
            return hotFallback(previous, kind, event, recoveryCommitText)
        } catch (_: LinkageError) {
            return hotFallback(previous, kind, event, recoveryCommitText)
        }
        if (
            transition.requestSequence != nextSequence ||
            transition.state.revision != previous.revision + 1
        ) {
            return hotFallback(previous, kind, event, recoveryCommitText)
        }
        val applied = applyTransition(previous, transition)
        if (applied) updateCompositionHistory(kind, event, transition.state)
        return applied
    }

    /** Preserves the complete live preedit as one local commit before Direct fallback. */
    private fun hotFallback(
        previous: EngineState,
        kind: MutationKind,
        event: EngineKeyEvent?,
        recoveryCommitText: String?,
    ): Boolean {
        val rawComposition = compositionKeys.toString().ifEmpty { previous.preedit }
        val recoveredText = when (kind) {
            MutationKind.INPUT -> rawComposition + requireNotNull(event?.text)
            MutationKind.BACKSPACE -> rawComposition.dropLastCodePointCopy()
            MutationKind.SELECT -> recoveryCommitText ?: rawComposition
            MutationKind.PAGE,
            MutationKind.COMMIT,
            -> rawComposition
            MutationKind.CANCEL -> ""
        }
        val oldEngine = engine
        val oldId = sessionId
        val oldSequence = nextSequence
        engine = null
        sessionId = null
        state = EngineState.empty()
        nextSequence = 0
        if (oldEngine != null && oldId != null && oldSequence >= 2) {
            runCatching { oldEngine.endSession(oldId, oldSequence) }
        }
        val context = activeContext ?: return false
        if (!installDirect(context)) return false
        val connection = currentInputConnection ?: return false
        val applied = when {
            kind == MutationKind.CANCEL -> {
                clearEditorComposition()
                true
            }
            recoveredText.isNotEmpty() -> runCatching {
                connection.commitText(recoveredText, 1)
            }.getOrDefault(false)
            kind == MutationKind.BACKSPACE && previous.preedit.isEmpty() -> runCatching {
                connection.deleteSurroundingTextInCodePoints(1, 0)
            }.getOrDefault(false)
            else -> {
                clearEditorComposition()
                true
            }
        }
        compositionKeys.clear()
        renderCandidates()
        return applied
    }

    private fun updateCompositionHistory(
        kind: MutationKind,
        event: EngineKeyEvent?,
        resultingState: EngineState,
    ) {
        when (kind) {
            MutationKind.INPUT -> if (resultingState.preedit.isEmpty()) {
                compositionKeys.clear()
            } else {
                compositionKeys.append(requireNotNull(event?.text))
            }
            MutationKind.BACKSPACE -> if (resultingState.preedit.isEmpty()) {
                compositionKeys.clear()
            } else {
                compositionKeys.dropLastCodePoint()
            }
            MutationKind.SELECT -> if (resultingState.preedit.isEmpty()) compositionKeys.clear()
            MutationKind.COMMIT,
            MutationKind.CANCEL,
            -> compositionKeys.clear()
            MutationKind.PAGE -> Unit
        }
    }

    private fun applyTransition(previous: EngineState, transition: EngineTransition): Boolean {
        val connection = currentInputConnection ?: return false
        val applied = try {
            connection.beginBatchEdit()
            try {
                when {
                    transition.commitText != null ->
                        connection.commitText(transition.commitText, 1)
                    transition.deleteBeforeCodePoints > 0 && previous.preedit.isEmpty() ->
                        connection.deleteSurroundingTextInCodePoints(
                            transition.deleteBeforeCodePoints,
                            0,
                        )
                    previous.preedit != transition.state.preedit ->
                        if (transition.state.preedit.isEmpty()) {
                            connection.setComposingText("", 1) &&
                                connection.finishComposingText()
                        } else {
                            connection.setComposingText(transition.state.preedit, 1)
                        }
                    else -> true
                }
            } finally {
                connection.endBatchEdit()
            }
        } catch (_: RuntimeException) {
            false
        }
        if (!applied) return false
        state = transition.state
        nextSequence += 1
        renderCandidates()
        return true
    }

    private fun clearEditorComposition() {
        val connection = currentInputConnection ?: return
        runCatching {
            connection.beginBatchEdit()
            try {
                connection.setComposingText("", 1)
                connection.finishComposingText()
            } finally {
                connection.endBatchEdit()
            }
        }
    }

    private fun finishEngine(clearEditorComposition: Boolean) {
        val activeEngine = engine
        val activeId = sessionId
        val terminalSequence = nextSequence
        val hadComposition = state.preedit.isNotEmpty()
        engine = null
        sessionId = null
        state = EngineState.empty()
        nextSequence = 0
        waitingForRime = false
        val hadBufferedComposition = compositionKeys.isNotEmpty()
        compositionKeys.clear()
        if (clearEditorComposition && (hadComposition || hadBufferedComposition)) clearEditorComposition()
        if (activeEngine != null && activeId != null && terminalSequence >= 2) {
            runCatching { activeEngine.endSession(activeId, terminalSequence) }
        }
        renderCandidates()
    }

    private fun requestRuntimeSnapshotIfAllowed() {
        val context = activeContext ?: return
        if (!context.clipVaultAllowed) {
            runtimeClient?.cancelPending()
            runtimeCandidates = emptyList()
            renderRuntimeCandidates()
            return
        }
        val token = inputGeneration
        runtimeClient?.request(limit = 8) { snapshot ->
            val current = activeContext
            if (
                token != inputGeneration ||
                current == null ||
                !current.clipVaultAllowed
            ) return@request
            runtimeCandidates = snapshot
            renderRuntimeCandidates()
            val first = snapshot.firstOrNull() ?: return@request
            val publisherEpoch = first.publisherEpoch
            val snapshotGeneration = first.snapshotGeneration
            val delay = (first.expiresAtElapsedMs - SystemClock.elapsedRealtime()).coerceAtLeast(0L)
            mainHandler.postDelayed(
                {
                    if (
                        token == inputGeneration &&
                        runtimeCandidates.firstOrNull()?.let { currentCandidate ->
                            currentCandidate.publisherEpoch == publisherEpoch &&
                                currentCandidate.snapshotGeneration == snapshotGeneration
                        } == true
                    ) clearRuntimeSurface()
                },
                delay,
            )
        }
    }

    private fun clearRuntimeSurface() {
        runtimeCandidates = emptyList()
        renderRuntimeCandidates()
    }

    private fun renderCandidates() {
        val host = candidateHost ?: return
        host.removeAllViews()
        if (state.hasPreviousPage) {
            host.addView(key("‹", accessibilityLabel = "上一页") { page(PageDirection.PREVIOUS) })
        }
        state.candidates.forEach { candidate ->
            host.addView(key(candidate.text) { select(candidate) })
        }
        if (state.hasNextPage) {
            host.addView(key("›", accessibilityLabel = "下一页") { page(PageDirection.NEXT) })
        }
        if (host.childCount == 0) {
            host.addView(TextView(this).apply {
                text = when {
                    waitingForRime -> "中文引擎后台准备中，按键将在本地内存暂存"
                    RimeEngineFactory.isReady() -> "本地中文输入"
                    else -> "中文引擎不可用，已安全降级为英文直输"
                }
                setPadding(dp(8), dp(6), dp(8), dp(6))
            })
        }
    }

    private fun renderRuntimeCandidates() {
        val host = runtimeHost ?: return
        host.removeAllViews()
        runtimeCandidates.forEach { candidate ->
            val display = "${candidate.label} ${candidate.text.replace("\n", " ").take(24)}"
            val boundPublisherEpoch = candidate.publisherEpoch
            val boundSnapshotGeneration = candidate.snapshotGeneration
            val boundCandidateId = candidate.candidateId
            val boundText = candidate.text
            host.addView(key(display) {
                val context = activeContext
                val stillCurrent = runtimeCandidates.any { currentCandidate ->
                    currentCandidate.publisherEpoch == boundPublisherEpoch &&
                        currentCandidate.snapshotGeneration == boundSnapshotGeneration &&
                        currentCandidate.candidateId == boundCandidateId &&
                        currentCandidate.text == boundText
                }
                if (
                    context != null &&
                    context.clipVaultAllowed &&
                    stillCurrent &&
                    SystemClock.elapsedRealtime() < candidate.expiresAtElapsedMs
                ) {
                    if (state.preedit.isEmpty() || commitComposition()) {
                        val committed = currentInputConnection?.commitText(boundText, 1) == true
                        if (committed) {
                            compositionKeys.clear()
                            clearRuntimeSurface()
                        }
                    }
                } else if (!stillCurrent || SystemClock.elapsedRealtime() >= candidate.expiresAtElapsedMs) {
                    clearRuntimeSurface()
                }
            })
        }
        runtimeScroll?.visibility = if (host.childCount == 0) View.GONE else View.VISIBLE
    }

    private fun renderKeyboard() {
        val host = keyboardHost ?: return
        host.removeAllViews()
        if (layoutMode == LayoutMode.LETTERS) renderLetters(host) else renderSymbols(host)
    }

    private fun renderLetters(host: LinearLayout) {
        addTextRow(host, "qwertyuiop")
        addTextRow(host, "asdfghjkl")
        val third = row()
        third.addView(key(shiftLabel(), accessibilityLabel = "Shift") { toggleShift() })
        "zxcvbnm".forEach { letter ->
            val value = if (shiftState == ShiftState.OFF) letter.toString() else letter.uppercase()
            third.addView(key(value) { inputLetter(value) })
        }
        third.addView(deleteKey())
        host.addView(third)
        addBottomRow(host)
    }

    private fun renderSymbols(host: LinearLayout) {
        addTextRow(host, "1234567890")
        addTextRow(host, "@#$%&-+()")
        val third = row()
        listOf("_", "=", "[", "]", "{", "}", "!", "?", "/").forEach { symbol ->
            third.addView(key(symbol) { input(symbol) })
        }
        third.addView(deleteKey())
        host.addView(third)
        addBottomRow(host)
    }

    private fun addTextRow(host: LinearLayout, values: String) {
        val row = row()
        values.forEach { value ->
            val text = if (
                layoutMode == LayoutMode.LETTERS && shiftState != ShiftState.OFF
            ) value.uppercase() else value.toString()
            row.addView(key(text) {
                if (layoutMode == LayoutMode.LETTERS) inputLetter(text) else input(text)
            })
        }
        host.addView(row)
    }

    private fun addBottomRow(host: LinearLayout) {
        val bottom = row()
        bottom.addView(key(if (layoutMode == LayoutMode.LETTERS) "?123" else "ABC") {
            layoutMode = if (layoutMode == LayoutMode.LETTERS) LayoutMode.SYMBOLS else LayoutMode.LETTERS
            shiftState = ShiftState.OFF
            renderKeyboard()
        })
        bottom.addView(key(languageLabel(), accessibilityLabel = "中英文切换") {
            toggleLanguage()
        })
        bottom.addView(key("🌐", accessibilityLabel = "切换输入法") {
            switchInputMethod()
        })
        bottom.addView(key(",") { input(",") })
        bottom.addView(key("空格", weight = 3f, accessibilityLabel = "空格") { input(" ") })
        bottom.addView(key(".") { input(".") })
        bottom.addView(key(editorActionLabel(), accessibilityLabel = "编辑器动作") { enter() })
        host.addView(bottom)
    }

    private fun languageLabel(): String = if (
        forceEnglishMode || activeContext?.fieldKind != EngineFieldKind.TEXT
    ) "英" else "中"

    private fun toggleLanguage() {
        val context = activeContext ?: return
        if (context.fieldKind != EngineFieldKind.TEXT) return
        if (!commitComposition()) return
        finishEngine(clearEditorComposition = false)
        forceEnglishMode = !forceEnglishMode
        startEngine(context)
        renderCandidates()
        renderKeyboard()
    }

    private fun switchInputMethod() {
        val switched = runCatching { switchToNextInputMethod(false) }.getOrDefault(false)
        if (!switched) {
            getSystemService(InputMethodManager::class.java)?.showInputMethodPicker()
        }
    }

    private fun inputLetter(text: String) {
        input(text)
        if (shiftState == ShiftState.ONCE) {
            shiftState = ShiftState.OFF
            renderKeyboard()
        }
    }

    private fun toggleShift() {
        val now = SystemClock.elapsedRealtime()
        shiftState = when {
            shiftState == ShiftState.LOCKED -> ShiftState.OFF
            shiftState == ShiftState.ONCE && now - lastShiftTapAt <= 500L -> ShiftState.LOCKED
            else -> ShiftState.ONCE
        }
        lastShiftTapAt = now
        renderKeyboard()
    }

    private fun shiftLabel(): String = when (shiftState) {
        ShiftState.OFF -> "⇧"
        ShiftState.ONCE -> "⇧"
        ShiftState.LOCKED -> "⇪"
    }

    private fun enter(): Boolean {
        if (!commitComposition()) return false
        val connection = currentInputConnection ?: return false
        val handled = if (
            editorAction != EditorInfo.IME_ACTION_NONE &&
            editorAction != EditorInfo.IME_ACTION_UNSPECIFIED
        ) {
            connection.performEditorAction(editorAction)
        } else {
            false
        }
        if (handled) return true
        val down = connection.sendKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_ENTER))
        if (!down) return false
        connection.sendKeyEvent(KeyEvent(KeyEvent.ACTION_UP, KeyEvent.KEYCODE_ENTER))
        return true
    }

    private fun editorActionLabel(): String = when (editorAction) {
        EditorInfo.IME_ACTION_GO -> "前往"
        EditorInfo.IME_ACTION_SEARCH -> "搜索"
        EditorInfo.IME_ACTION_SEND -> "发送"
        EditorInfo.IME_ACTION_NEXT -> "下一项"
        EditorInfo.IME_ACTION_DONE -> "完成"
        EditorInfo.IME_ACTION_PREVIOUS -> "上一项"
        else -> "回车"
    }

    private fun inputContext(info: EditorInfo?): EngineInputContext {
        val inputType = info?.inputType ?: InputType.TYPE_CLASS_TEXT
        val variation = inputType and InputType.TYPE_MASK_VARIATION
        val password = variation == InputType.TYPE_TEXT_VARIATION_PASSWORD ||
            variation == InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD ||
            variation == InputType.TYPE_TEXT_VARIATION_WEB_PASSWORD ||
            variation == InputType.TYPE_NUMBER_VARIATION_PASSWORD
        val sensitiveApp = sensitiveAppPolicy.isSensitive(info?.packageName)
        val incognito = sensitiveApp || (info?.imeOptions ?: 0) and
            EditorInfo.IME_FLAG_NO_PERSONALIZED_LEARNING != 0
        val kind = when {
            password -> EngineFieldKind.PASSWORD
            inputType and InputType.TYPE_MASK_CLASS == InputType.TYPE_CLASS_NUMBER -> EngineFieldKind.NUMBER
            inputType and InputType.TYPE_MASK_CLASS == InputType.TYPE_CLASS_PHONE -> EngineFieldKind.PHONE
            variation == InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS -> EngineFieldKind.EMAIL
            variation == InputType.TYPE_TEXT_VARIATION_URI -> EngineFieldKind.URL
            else -> EngineFieldKind.TEXT
        }
        val personal = !password && !incognito
        return EngineInputContext(kind, incognito, personal, personal)
    }

    private fun row() = LinearLayout(this).apply {
        orientation = LinearLayout.HORIZONTAL
    }

    private fun key(
        label: String,
        weight: Float = 1f,
        accessibilityLabel: String = label,
        action: () -> Unit,
    ) = Button(this).apply {
        text = label
        isAllCaps = false
        contentDescription = accessibilityLabel
        layoutParams = LinearLayout.LayoutParams(0, dp(48), weight)
        setOnClickListener {
            if (ImePreferences.hapticFeedbackEnabled(this@ClipVaultIsolatedImeService)) {
                performHapticFeedback(HapticFeedbackConstants.KEYBOARD_TAP)
            }
            action()
        }
    }

    private fun deleteKey() = Button(this).apply {
        text = "⌫"
        isAllCaps = false
        contentDescription = "删除"
        layoutParams = LinearLayout.LayoutParams(0, dp(48), 1f)
        setOnTouchListener { view, event ->
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    if (ImePreferences.hapticFeedbackEnabled(this@ClipVaultIsolatedImeService)) {
                        view.performHapticFeedback(HapticFeedbackConstants.KEYBOARD_TAP)
                    }
                    backspaceRepeater.press()
                    true
                }
                MotionEvent.ACTION_UP,
                MotionEvent.ACTION_CANCEL,
                -> {
                    backspaceRepeater.release()
                    true
                }
                else -> true
            }
        }
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()

    private fun StringBuilder.dropLastCodePoint() {
        if (isEmpty()) return
        val codePoint = Character.codePointBefore(this, length)
        delete(length - Character.charCount(codePoint), length)
    }

    private fun String.dropLastCodePointCopy(): String {
        if (isEmpty()) return this
        val codePoint = codePointBefore(length)
        return dropLast(Character.charCount(codePoint))
    }

    private companion object {
        const val RIME_READY_POLL_MS = 50L
        const val RIME_WAIT_TIMEOUT_MS = 15_000L
    }
}
