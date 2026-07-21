package com.clipvault.app.ime

import android.inputmethodservice.InputMethodService
import android.view.KeyEvent
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputConnection
import android.widget.Button
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.view.ViewCompat
import com.clipvault.app.runtime.ClipVaultFacade
import com.clipvault.app.runtime.ClipVaultRuntime
import kotlin.concurrent.thread

/**
 * ClipVault Full Keyboard Lab (ROADMAP_V2 PR4 — experimental second IME).
 *
 * A real (if basic) English keyboard so ClipVault can be a primary input entry,
 * plus a ClipVault toolbar/candidate strip that pastes Runtime candidates.
 *
 * PRIVACY: this service never persists keystrokes. Keys only drive the current
 * InputConnection. ClipVault candidates are hidden in sensitive fields.
 */
class ClipVaultFullKeyboardService : InputMethodService() {

    private val runtime: ClipVaultFacade by lazy { ClipVaultRuntime.facade(this) }
    private var shifted = false
    private var symbols = false
    private val privacySession = ImePrivacySession()
    private lateinit var keys: LinearLayout
    private lateinit var candidates: LinearLayout
    private var editorAction = ImeEditorAction.NEW_LINE

    private val letterRows = listOf("qwertyuiop", "asdfghjkl", "zxcvbnm")
    private val symbolRows = listOf("1234567890", "@#\$%&-+()/", "*\"':;!?,.")

    override fun onStartInput(attribute: EditorInfo?, restarting: Boolean) {
        super.onStartInput(attribute, restarting)
        editorAction = ImeEditorActionResolver.resolve(
            attribute?.imeOptions ?: EditorInfo.IME_ACTION_UNSPECIFIED,
        )
        val wasAllowed = privacySession.allowsPersonalData()
        privacySession.begin(PrivacyAwareFilter.shouldSuppressCandidates(attribute))
        if (::keys.isInitialized) renderKeys()
        if (::candidates.isInitialized) {
            if (!privacySession.allowsPersonalData()) {
                candidates.removeAllViews()
                candidates.addView(hint(PrivacyAwareFilter.suppressionMessage()))
            } else if (!wasAllowed) {
                candidates.removeAllViews()
                candidates.addView(hint("点 ClipVault 调取候选 →"))
            }
            // Same ordinary editor generation keeps already-rendered candidates.
        }
    }

    override fun onFinishInput() {
        privacySession.end()
        editorAction = ImeEditorAction.NEW_LINE
        if (::candidates.isInitialized) {
            candidates.removeAllViews()
            candidates.addView(hint(PrivacyAwareFilter.suppressionMessage()))
        }
        super.onFinishInput()
    }

    override fun onCreateInputView(): View {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(2), dp(4), dp(2), dp(6))
        }

        val toolbar = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        toolbar.addView(key("📋 ClipVault", weight = 2f, accessibilityLabel = "打开 ClipVault 候选") { showCandidates() })
        toolbar.addView(key("切回", weight = 1f, accessibilityLabel = "切回上一个输入法") {
            switchToPreviousInputMethodCompat()
        })
        root.addView(toolbar)

        val strip = HorizontalScrollView(this)
        candidates = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        candidates.addView(hint(if (privacySession.allowsPersonalData()) "点 ClipVault 调取候选 →" else PrivacyAwareFilter.suppressionMessage()))
        strip.addView(candidates)
        root.addView(strip)

        keys = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        root.addView(keys)
        renderKeys()
        return root
    }

    private fun renderKeys() {
        keys.removeAllViews()
        val rows = if (symbols) symbolRows else letterRows
        rows.forEachIndexed { i, row ->
            val r = rowLayout()
            if (!symbols && i == rows.lastIndex) {
                r.addView(key("⇧", weight = 1.5f, accessibilityLabel = "大写", active = shifted) {
                    shifted = !shifted
                    renderKeys()
                })
            }
            row.forEach { ch ->
                val label = if (!symbols && shifted) ch.uppercaseChar() else ch
                r.addView(key(label.toString(), weight = 1f) {
                    commit(label.toString())
                    if (shifted && !symbols) { shifted = false; renderKeys() }
                })
            }
            if (i == rows.lastIndex) {
                r.addView(key("⌫", weight = 1.5f, accessibilityLabel = "删除") { backspace() })
            }
            keys.addView(r)
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
        bottom.addView(key(",", weight = 1f) { commit(",") })
        bottom.addView(key("空格", weight = 4f, accessibilityLabel = "空格") { commit(" ") })
        bottom.addView(key(".", weight = 1f) { commit(".") })
        bottom.addView(
            key(
                editorAction.keyLabel,
                weight = 1.5f,
                accessibilityLabel = editorAction.accessibilityLabel,
            ) { performEditorActionOrEnter() },
        )
        keys.addView(bottom)
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
            val items = runtime.listCandidates(limit = 20)
            runOnMain {
                if (!privacySession.isCurrent(token)) return@runOnMain
                if (!privacySession.allowsPersonalData(token)) {
                    candidates.removeAllViews()
                    candidates.addView(hint(PrivacyAwareFilter.suppressionMessage()))
                    return@runOnMain
                }
                candidates.removeAllViews()
                if (items.isEmpty()) {
                    candidates.addView(hint("（暂无候选，先在桌面添加记忆或复制内容并同步）"))
                } else {
                    items.forEach { c ->
                        candidates.addView(key("${c.label} " + c.text.replace("\n", " ").take(24), weight = 0f) {
                            if (privacySession.allowsPersonalData()) commit(c.text)
                        })
                    }
                }
            }
        }
    }

    private fun commit(s: String) { currentInputConnection?.commitText(s, 1) }
    private fun backspace() { currentInputConnection?.deleteSurroundingText(1, 0) }
    private fun performEditorActionOrEnter() {
        val ic = currentInputConnection ?: return
        editorAction.perform(
            performEditorAction = { actionId -> ic.performEditorAction(actionId) },
            sendEnter = { sendEnterKeyEvent(ic) },
        )
    }

    private fun sendEnterKeyEvent(ic: InputConnection) {
        ic.sendKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_ENTER))
        ic.sendKeyEvent(KeyEvent(KeyEvent.ACTION_UP, KeyEvent.KEYCODE_ENTER))
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
    ): Button =
        Button(this).apply {
            text = label; isAllCaps = false; textSize = 16f
            contentDescription = accessibilityLabel
            active?.let { isActive ->
                isActivated = isActive
                isSelected = isActive
                ViewCompat.setStateDescription(this, if (isActive) "已开启" else "已关闭")
            }
            setPadding(dp(2), 0, dp(2), 0)
            layoutParams = if (weight > 0f) LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, weight)
                else LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, dp(40))
            setOnClickListener { onClick() }
        }

    private fun hint(text: String) = TextView(this).apply {
        this.text = text; textSize = 12f; setPadding(dp(8), dp(8), dp(8), dp(8))
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()
    private fun runOnMain(block: () -> Unit) = android.os.Handler(mainLooper).post(block)
}
