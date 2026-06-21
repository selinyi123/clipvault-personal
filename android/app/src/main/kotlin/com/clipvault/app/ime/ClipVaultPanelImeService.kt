package com.clipvault.app.ime

import android.content.ClipboardManager
import android.content.Context
import android.inputmethodservice.InputMethodService
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import com.clipvault.app.runtime.Candidate
import com.clipvault.app.runtime.ClipVaultFacade
import com.clipvault.app.runtime.ClipVaultRuntime
import kotlin.concurrent.thread

/**
 * ClipVault Panel IME — the companion knowledge panel (ADR-0004, renamed in
 * ROADMAP_V2 PR3 to distinguish it from the v2 ClipVaultFullKeyboardService).
 * Recent clips + synced desktop clips + memory panels, one-tap paste, a
 * save-clipboard button, and a key to switch back to the previous keyboard.
 *
 * PRIVACY (G2 / THREAT_MODEL T4): this service NEVER records typed text. It
 * has no onUpdateSelection/key persistence path; the only write happens when
 * the user explicitly taps "保存剪贴板". There are no network calls from here
 * (sync is delegated to WorkManager, which only moves already-stored data).
 */
class ClipVaultPanelImeService : InputMethodService() {

    // All data access goes through the Runtime facade (ADR-0008), never the DAO.
    private val runtime: ClipVaultFacade by lazy { ClipVaultRuntime.facade(this) }
    private var suppressCandidates = false

    override fun onStartInput(attribute: EditorInfo?, restarting: Boolean) {
        super.onStartInput(attribute, restarting)
        suppressCandidates = PrivacyAwareFilter.shouldSuppressCandidates(attribute)
    }

    override fun onCreateInputView(): View {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(16, 12, 16, 12)
        }

        // Action row: save current clipboard / switch back
        val actions = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        actions.addView(button("保存剪贴板") { saveClipboard() })
        actions.addView(button("切回键盘") { switchToPreviousInputMethod() })
        root.addView(actions)

        // Panel switcher: recent clips + memory categories (词库/Prompt/命令).
        val tabs = HorizontalScrollView(this)
        val tabRow = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        tabs.addView(tabRow)
        root.addView(tabs)

        val scroll = ScrollView(this)
        val list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        scroll.addView(list)
        root.addView(scroll, ViewGroup.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, dp(220)))

        // tab -> loader
        val panels = linkedMapOf<String, () -> Unit>(
            "最近" to {
                showCandidates(
                    list = list,
                    source = "clip",
                    kind = null,
                    title = "最近内容（点按粘贴）",
                    emptyMessage = "（暂无内容，先在桌面复制或分享到 ClipVault）",
                    limit = 40,
                )
            },
            "词库" to { showMemoryCandidates(list, "term") },
            "短语" to { showMemoryCandidates(list, "phrase") },
            "Prompt" to { showMemoryCandidates(list, "prompt") },
            "命令" to { showMemoryCandidates(list, "command") },
        )
        panels.forEach { (label, loader) ->
            tabRow.addView(button(label) { loader() })
        }
        panels.getValue("最近").invoke()   // default panel
        return root
    }

    private fun showMemoryCandidates(list: LinearLayout, kind: String) {
        showCandidates(
            list = list,
            source = "memory",
            kind = kind,
            title = "$kind（点按粘贴）",
            emptyMessage = "（暂无 $kind，可在桌面词库添加并同步）",
            limit = 100,
        )
    }

    private fun showCandidates(
        list: LinearLayout,
        source: String,
        kind: String?,
        title: String,
        emptyMessage: String,
        limit: Int,
    ) {
        if (suppressCandidates) {
            showSuppressed(list)
            return
        }
        thread {
            val items = runtime.listCandidates(limit = 200)
                .filter { c -> c.source == source && (kind == null || c.kind == kind) }
                .take(limit)
            runOnMain {
                list.removeAllViews()
                list.addView(TextView(this).apply { text = title; textSize = 12f })
                items.forEach { c -> list.addView(candidateButton(c)) }
                if (items.isEmpty()) list.addView(TextView(this).apply { text = emptyMessage })
            }
        }
    }

    private fun candidateButton(c: Candidate): Button =
        button("${c.label} " + c.text.replace("\n", " ").take(48)) {
            currentInputConnection?.commitText(c.text, 1)   // one-tap paste
        }

    private fun showSuppressed(list: LinearLayout) {
        list.removeAllViews()
        list.addView(TextView(this).apply {
            text = PrivacyAwareFilter.suppressionMessage()
            textSize = 12f
        })
    }

    private fun saveClipboard() {
        // The IME is the current input method, so reading the clipboard is allowed.
        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val text = cm.primaryClip?.takeIf { it.itemCount > 0 }
            ?.getItemAt(0)?.coerceToText(this)?.toString()
        if (text.isNullOrBlank()) return
        thread {
            runtime.saveExplicit(text, sourceDevice = android.os.Build.MODEL ?: "android")
        }
    }

    private fun button(label: String, onClick: () -> Unit): Button =
        Button(this).apply { text = label; isAllCaps = false; setOnClickListener { onClick() } }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()

    private fun runOnMain(block: () -> Unit) =
        android.os.Handler(mainLooper).post(block)
}
