package com.clipvault.app.ime

import android.inputmethodservice.InputMethodService
import android.view.KeyEvent
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.TextView
import com.clipvault.app.runtime.ClipVaultFacade
import com.clipvault.app.runtime.ClipVaultRuntime
import kotlin.concurrent.thread

/**
 * ClipVault Full Keyboard Lab (ROADMAP_V2 PR4 — experimental second IME).
 *
 * A real (if basic) English keyboard so ClipVault can be a *primary* input
 * entry, plus a ClipVault toolbar/candidate strip that pastes recent clips via
 * the Runtime facade. No Chinese engine yet — the Rime/Fcitx5 base is a later
 * spike (PR5). The Panel IME (ClipVaultPanelImeService) is unchanged.
 *
 * PRIVACY: like the panel, this never persists keystrokes — keys only drive the
 * current InputConnection. The only stored write is the explicit toolbar action,
 * which goes through the same Runtime facade.
 */
class ClipVaultFullKeyboardService : InputMethodService() {

    private val runtime: ClipVaultFacade by lazy { ClipVaultRuntime.facade(this) }
    private var shifted = false
    private var symbols = false
    private lateinit var keys: LinearLayout
    private lateinit var candidates: LinearLayout

    private val letterRows = listOf("qwertyuiop", "asdfghjkl", "zxcvbnm")
    private val symbolRows = listOf("1234567890", "@#\$%&-+()/", "*\"':;!?,.")

    override fun onCreateInputView(): View {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(2), dp(4), dp(2), dp(6))
        }

        // ClipVault toolbar
        val toolbar = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        toolbar.addView(key("📋 最近剪切板", weight = 2f) { showRecentClips() })
        toolbar.addView(key("切回", weight = 1f) { switchToPreviousInputMethod() })
        root.addView(toolbar)

        // Candidate / ClipVault strip (filled when 最近 is tapped)
        val strip = HorizontalScrollView(this)
        candidates = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        candidates.addView(hint("点「最近剪切板」调取你存过的内容 →"))
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
            // last letter row gets shift (left) and backspace (right)
            if (!symbols && i == rows.lastIndex) {
                r.addView(key(if (shifted) "⇧" else "⇪", weight = 1.5f) { shifted = !shifted; renderKeys() })
            }
            row.forEach { ch ->
                val label = if (!symbols && shifted) ch.uppercaseChar() else ch
                r.addView(key(label.toString(), weight = 1f) {
                    commit(label.toString())
                    if (shifted && !symbols) { shifted = false; renderKeys() }  // one-shot shift
                })
            }
            if (i == rows.lastIndex) {
                r.addView(key("⌫", weight = 1.5f) { backspace() })
            }
            keys.addView(r)
        }
        // bottom row
        val bottom = rowLayout()
        bottom.addView(key(if (symbols) "ABC" else "?123", weight = 1.5f) { symbols = !symbols; renderKeys() })
        bottom.addView(key(",", weight = 1f) { commit(",") })
        bottom.addView(key("空格", weight = 4f) { commit(" ") })
        bottom.addView(key(".", weight = 1f) { commit(".") })
        bottom.addView(key("⏎", weight = 1.5f) { enter() })
        keys.addView(bottom)
    }

    private fun showRecentClips() {
        thread {
            val clips = runtime.listRecentClips(20)   // facade is crash-safe
            runOnMain {
                candidates.removeAllViews()
                if (clips.isEmpty()) {
                    candidates.addView(hint("（暂无内容，先在桌面复制或分享到 ClipVault）"))
                } else {
                    clips.forEach { c ->
                        candidates.addView(key(c.text.replace("\n", " ").take(24), weight = 0f) {
                            commit(c.text)
                        })
                    }
                }
            }
        }
    }

    // --- input helpers ---
    private fun commit(s: String) { currentInputConnection?.commitText(s, 1) }
    private fun backspace() { currentInputConnection?.deleteSurroundingText(1, 0) }
    private fun enter() {
        val ic = currentInputConnection ?: return
        ic.sendKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_ENTER))
        ic.sendKeyEvent(KeyEvent(KeyEvent.ACTION_UP, KeyEvent.KEYCODE_ENTER))
    }

    // --- view helpers ---
    private fun rowLayout() = LinearLayout(this).apply {
        orientation = LinearLayout.HORIZONTAL
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(46))
    }

    private fun key(label: String, weight: Float, onClick: () -> Unit): Button =
        Button(this).apply {
            text = label; isAllCaps = false; textSize = 16f
            setPadding(dp(2), 0, dp(2), 0)
            layoutParams =
                if (weight > 0f) LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, weight)
                else LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, dp(40))
            setOnClickListener { onClick() }
        }

    private fun hint(text: String) = TextView(this).apply {
        this.text = text; textSize = 12f; setPadding(dp(8), dp(8), dp(8), dp(8))
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()
    private fun runOnMain(block: () -> Unit) = android.os.Handler(mainLooper).post(block)
}
