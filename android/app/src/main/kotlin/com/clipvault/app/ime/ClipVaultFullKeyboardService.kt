package com.clipvault.app.ime

import android.inputmethodservice.InputMethodService
import android.os.Build
import android.os.Bundle
import android.util.Size
import android.view.KeyEvent
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputConnection
import android.widget.inline.InlinePresentationSpec
import android.view.inputmethod.InlineSuggestionsRequest
import android.view.inputmethod.InlineSuggestionsResponse
import android.widget.Button
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.annotation.RequiresApi
import androidx.core.view.ViewCompat
import com.clipvault.app.runtime.ClipVaultFacade
import com.clipvault.app.runtime.ClipVaultRuntime
import com.clipvault.app.ime.rime.RimeEngineFactory
import com.clipvault.ime.engine.EngineFieldKind
import com.clipvault.ime.engine.EngineInputContext
import kotlin.concurrent.thread

/**
 * Local-first ClipVault keyboard shell.
 *
 * Every printable key is routed through the production Engine Protocol V2
 * session boundary before it reaches InputConnection. The current local direct
 * adapter keeps the keyboard usable without Runtime, network, or native engine
 * availability; a licensed native librime adapter can replace that factory
 * without changing this service or its editor/privacy boundary.
 *
 * PRIVACY: this service never persists or logs typed text, never observes
 * surrounding text, and hides personal ClipVault candidates in sensitive
 * fields. Runtime candidates are an independent toolbar and are never required
 * for ordinary keyboard input.
 */
class ClipVaultFullKeyboardService : InputMethodService() {

    private val runtime: ClipVaultFacade by lazy { ClipVaultRuntime.facade(this) }
    private val privacySession = ImePrivacySession()
    private var activeEngineContext = EngineInputContext(
        fieldKind = EngineFieldKind.UNKNOWN,
        incognito = true,
        learningAllowed = false,
        clipVaultAllowed = false,
    )
    private val engineController by lazy {
        EngineSessionController(
            engineFactory = { RimeEngineFactory.create(this, activeEngineContext) },
            editor = AndroidInputConnectionEngineEditor { currentInputConnection },
            render = ::onEngineUiState,
        )
    }

    private var shifted = false
    private var symbols = false
    private var editorAction = ImeEditorAction.NEW_LINE
    private var engineUiState = EngineUiState(
        candidates = emptyList(),
        composing = false,
        engineAvailable = false,
    )
    private lateinit var keys: LinearLayout
    private var inlineSuggestionHost: LinearLayout? = null
    private var inlineSuggestionGeneration = 0L
    private lateinit var engineCandidates: LinearLayout
    private lateinit var candidates: LinearLayout

    private val letterRows = listOf("qwertyuiop", "asdfghjkl", "zxcvbnm")
    private val symbolRows = listOf("1234567890", "@#\$%&-+()/", "*\"':;!?,.")

    override fun onStartInput(attribute: EditorInfo?, restarting: Boolean) {
        super.onStartInput(attribute, restarting)
        editorAction = ImeEditorActionResolver.resolve(
            attribute?.imeOptions ?: EditorInfo.IME_ACTION_UNSPECIFIED,
        )
        val wasAllowed = privacySession.allowsPersonalData()
        privacySession.begin(PrivacyAwareFilter.shouldSuppressCandidates(attribute))
        activeEngineContext = AndroidEngineInputContext.from(attribute)
        engineController.begin(activeEngineContext)
        if (::keys.isInitialized) renderKeys()
        if (::candidates.isInitialized) {
            if (!privacySession.allowsPersonalData()) {
                candidates.removeAllViews()
                candidates.addView(hint(PrivacyAwareFilter.suppressionMessage()))
            } else if (!wasAllowed) {
                candidates.removeAllViews()
                candidates.addView(hint("点击 ClipVault 调取候选 →"))
            }
        }
    }

    override fun onFinishInput() {
        engineController.finish()
        privacySession.end()
        editorAction = ImeEditorAction.NEW_LINE
        if (::candidates.isInitialized) {
            candidates.removeAllViews()
            candidates.addView(hint(PrivacyAwareFilter.suppressionMessage()))
        }
        super.onFinishInput()
    }

    override fun onDestroy() {
        engineController.finish()
        privacySession.end()
        super.onDestroy()
    }

    override fun onCreateInputView(): View {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(2), dp(4), dp(2), dp(6))
        }

        inlineSuggestionHost = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            visibility = View.GONE
            contentDescription = "System Autofill suggestions"
        }
        root.addView(inlineSuggestionHost)

        val engineStrip = HorizontalScrollView(this)
        engineCandidates = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        engineStrip.addView(engineCandidates)
        root.addView(engineStrip)
        renderEngineCandidates()

        val toolbar = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        toolbar.addView(
            key(
                "📋 ClipVault",
                weight = 2f,
                accessibilityLabel = "打开 ClipVault 候选",
            ) { showCandidates() },
        )
        toolbar.addView(
            key("切回", weight = 1f, accessibilityLabel = "切回上一个输入法") {
                switchToPreviousInputMethodCompat()
            },
        )
        root.addView(toolbar)

        val clipVaultStrip = HorizontalScrollView(this)
        candidates = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        candidates.addView(
            hint(
                if (privacySession.allowsPersonalData()) {
                    "点击 ClipVault 调取候选 →"
                } else {
                    PrivacyAwareFilter.suppressionMessage()
                },
            ),
        )
        clipVaultStrip.addView(candidates)
        root.addView(clipVaultStrip)

        keys = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        root.addView(keys)
        renderKeys()
        return root
    }

    /**
     * Requests opaque, system-rendered Autofill chips on Android 11+.
     * The IME controls only size/count; it never receives suggestion text.
     */
    @RequiresApi(Build.VERSION_CODES.R)
    override fun onCreateInlineSuggestionsRequest(uiExtras: Bundle): InlineSuggestionsRequest {
        check(InlineSuggestionPolicy.isSupported())
        val presentation = InlinePresentationSpec.Builder(
            Size(dp(48), dp(36)),
            Size(dp(320), dp(52)),
        ).build()
        return InlineSuggestionsRequest.Builder(listOf(presentation))
            .setMaxSuggestionCount(3)
            .setExtras(uiExtras)
            .build()
    }

    /** Hosts protected Views returned by Autofill; no text extraction is attempted. */
    @RequiresApi(Build.VERSION_CODES.R)
    override fun onInlineSuggestionsResponse(response: InlineSuggestionsResponse): Boolean {
        if (!InlineSuggestionPolicy.isSupported()) return false
        val host = inlineSuggestionHost ?: return false
        val generation = ++inlineSuggestionGeneration
        val suggestions = response.inlineSuggestions
        if (suggestions.isEmpty()) {
            host.removeAllViews()
            host.visibility = View.GONE
            return false
        }

        val views = arrayOfNulls<View>(suggestions.size)
        var completed = 0
        suggestions.forEachIndexed { index, suggestion ->
            suggestion.inflate(this, Size(dp(320), dp(48)), mainExecutor) { view ->
                if (generation != inlineSuggestionGeneration) return@inflate
                views[index] = view
                completed += 1
                if (completed == suggestions.size) {
                    host.removeAllViews()
                    views.filterNotNull().forEach(host::addView)
                    host.visibility = if (host.childCount == 0) View.GONE else View.VISIBLE
                }
            }
        }
        return true
    }

    private fun renderKeys() {
        keys.removeAllViews()
        val rows = if (symbols) symbolRows else letterRows
        rows.forEachIndexed { index, row ->
            val keyRow = rowLayout()
            if (!symbols && index == rows.lastIndex) {
                keyRow.addView(
                    key("⇧", weight = 1.5f, accessibilityLabel = "大写", active = shifted) {
                        shifted = !shifted
                        renderKeys()
                    },
                )
            }
            row.forEach { character ->
                val label = if (!symbols && shifted) character.uppercaseChar() else character
                keyRow.addView(
                    key(label.toString(), weight = 1f) {
                        engineController.inputText(label.toString())
                        if (shifted && !symbols) {
                            shifted = false
                            renderKeys()
                        }
                    },
                )
            }
            if (index == rows.lastIndex) {
                keyRow.addView(
                    key("⌫", weight = 1.5f, accessibilityLabel = "删除") {
                        engineController.backspace()
                    },
                )
            }
            keys.addView(keyRow)
        }

        val bottom = rowLayout()
        bottom.addView(
            key(
                if (symbols) "ABC" else "?123",
                weight = 1.5f,
                accessibilityLabel = "符号键盘",
                active = symbols,
            ) {
                symbols = !symbols
                renderKeys()
            },
        )
        bottom.addView(key(",", weight = 1f) { engineController.inputText(",") })
        bottom.addView(
            key("空格", weight = 4f, accessibilityLabel = "空格") {
                engineController.inputText(" ")
            },
        )
        bottom.addView(key(".", weight = 1f) { engineController.inputText(".") })
        bottom.addView(
            key(
                editorAction.keyLabel,
                weight = 1.5f,
                accessibilityLabel = editorAction.accessibilityLabel,
            ) { enter() },
        )
        keys.addView(bottom)
    }

    private fun onEngineUiState(state: EngineUiState) {
        engineUiState = state
        if (::engineCandidates.isInitialized) renderEngineCandidates()
    }

    private fun renderEngineCandidates() {
        engineCandidates.removeAllViews()
        when {
            engineUiState.candidates.isNotEmpty() -> engineUiState.candidates.forEach { candidate ->
                engineCandidates.addView(
                    key(
                        label = candidate.comment?.let { "${candidate.text}  $it" } ?: candidate.text,
                        weight = 0f,
                    ) { engineController.selectCandidate(candidate.id) },
                )
            }
            engineUiState.engineAvailable -> engineCandidates.addView(hint("本地直输模式"))
            else -> engineCandidates.addView(hint("引擎不可用，已降级为直接输入"))
        }
    }

    private fun showCandidates() {
        val token = privacySession.token()
        if (!privacySession.allowsPersonalData(token)) {
            candidates.removeAllViews()
            candidates.addView(hint(PrivacyAwareFilter.suppressionMessage()))
            return
        }
        thread {
            if (!privacySession.allowsPersonalData(token)) return@thread
            val items = try {
                runtime.listCandidates(limit = 20)
            } catch (_: RuntimeException) {
                emptyList()
            }
            runOnMain {
                if (!privacySession.isCurrent(token)) return@runOnMain
                if (!privacySession.allowsPersonalData(token)) {
                    candidates.removeAllViews()
                    candidates.addView(hint(PrivacyAwareFilter.suppressionMessage()))
                    return@runOnMain
                }
                candidates.removeAllViews()
                if (items.isEmpty()) {
                    candidates.addView(hint("Runtime 暂不可用或暂无候选；键盘输入不受影响"))
                } else {
                    items.forEach { candidate ->
                        candidates.addView(
                            key(
                                "${candidate.label} ${candidate.text.replace("\n", " ").take(24)}",
                                weight = 0f,
                            ) {
                                if (privacySession.allowsPersonalData()) {
                                    currentInputConnection?.commitText(candidate.text, 1)
                                }
                            },
                        )
                    }
                }
            }
        }
    }

    private fun enter() {
        val connection = currentInputConnection ?: return
        if (!engineController.commitComposition()) return
        editorAction.perform(
            performEditorAction = { actionId -> connection.performEditorAction(actionId) },
            sendEnter = { sendEnterKeyEvent(connection) },
        )
    }

    private fun sendEnterKeyEvent(connection: InputConnection) {
        connection.sendKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_ENTER))
        connection.sendKeyEvent(KeyEvent(KeyEvent.ACTION_UP, KeyEvent.KEYCODE_ENTER))
    }

    private fun rowLayout() = LinearLayout(this).apply {
        orientation = LinearLayout.HORIZONTAL
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(46))
    }

    private fun key(
        label: String,
        weight: Float,
        accessibilityLabel: String = label,
        active: Boolean? = null,
        onClick: () -> Unit,
    ): Button = Button(this).apply {
        text = label
        isAllCaps = false
        textSize = 16f
        contentDescription = accessibilityLabel
        active?.let { isActive ->
            isActivated = isActive
            isSelected = isActive
            ViewCompat.setStateDescription(this, if (isActive) "已开启" else "已关闭")
        }
        setPadding(dp(2), 0, dp(2), 0)
        layoutParams = if (weight > 0f) {
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, weight)
        } else {
            LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, dp(40))
        }
        setOnClickListener { onClick() }
    }

    private fun hint(text: String) = TextView(this).apply {
        this.text = text
        textSize = 12f
        setPadding(dp(8), dp(8), dp(8), dp(8))
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    private fun runOnMain(block: () -> Unit) =
        android.os.Handler(mainLooper).post(block)
}
