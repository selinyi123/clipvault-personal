package com.clipvault.app.ime

import android.content.ClipboardManager
import android.content.Context
import android.inputmethodservice.InputMethodService
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import com.clipvault.app.ClipVaultApp
import com.clipvault.app.capture.Capture
import com.clipvault.app.data.ClipEntity
import com.clipvault.app.sync.SyncScheduler
import kotlin.concurrent.thread

/**
 * ClipVault Keyboard Personal — a companion IME (ADR-0004). It is a knowledge
 * panel: recent clips + synced desktop clips, one-tap paste, a save-clipboard
 * button, and a key to switch back to the previous keyboard.
 *
 * PRIVACY (G2 / THREAT_MODEL T4): this service NEVER records typed text. It
 * has no onUpdateSelection/key persistence path; the only write happens when
 * the user explicitly taps "保存剪贴板". There are no network calls from here
 * (sync is delegated to WorkManager, which only moves already-stored data).
 */
class ClipVaultKeyboardService : InputMethodService() {

    override fun onCreateInputView(): View {
        val ctx = this
        val root = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(16, 12, 16, 12)
        }

        // Action row: save current clipboard / switch back
        val actions = LinearLayout(ctx).apply { orientation = LinearLayout.HORIZONTAL }
        actions.addView(button("保存剪贴板") { saveClipboard() })
        actions.addView(button("切回键盘") { switchToPreviousInputMethod() })
        root.addView(actions)

        // Panel switcher: recent clips + memory categories (词库/Prompt/命令).
        val tabs = HorizontalScrollView(ctx)
        val tabRow = LinearLayout(ctx).apply { orientation = LinearLayout.HORIZONTAL }
        tabs.addView(tabRow)
        root.addView(tabs)

        val scroll = ScrollView(ctx)
        val list = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL }
        scroll.addView(list)
        root.addView(scroll, ViewGroup.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, dp(220)))

        // tab -> loader
        val panels = linkedMapOf<String, () -> Unit>(
            "最近" to { showClips(list) },
            "词库" to { showMemory(list, "term") },
            "短语" to { showMemory(list, "phrase") },
            "Prompt" to { showMemory(list, "prompt") },
            "命令" to { showMemory(list, "command") },
        )
        panels.forEach { (label, loader) ->
            tabRow.addView(button(label) { loader() })
        }
        showClips(list)   // default panel
        return root
    }

    private fun showClips(list: LinearLayout) {
        val ctx = this
        thread {
            val clips = ClipVaultApp.db(ctx).clips().list("", 0)   // public only; never secrets
            runOnMain {
                list.removeAllViews()
                list.addView(TextView(ctx).apply { text = "最近内容（点按粘贴）"; textSize = 12f })
                clips.take(40).forEach { c -> list.addView(clipButton(c)) }
                if (clips.isEmpty()) list.addView(TextView(ctx).apply {
                    text = "（暂无内容，先在桌面复制或分享到 ClipVault）"
                })
            }
        }
    }

    private fun showMemory(list: LinearLayout, kind: String) {
        val ctx = this
        thread {
            val items = ClipVaultApp.db(ctx).memory().list(kind)
            runOnMain {
                list.removeAllViews()
                list.addView(TextView(ctx).apply { text = "$kind（点按粘贴）"; textSize = 12f })
                items.forEach { m ->
                    list.addView(button(m.text.replace("\n", " ").take(48)) {
                        currentInputConnection?.commitText(m.text, 1)
                    })
                }
                if (items.isEmpty()) list.addView(TextView(ctx).apply {
                    text = "（暂无 $kind，可在桌面词库添加并同步）"
                })
            }
        }
    }

    private fun clipButton(c: ClipEntity): Button =
        button("[${c.contentType}] " + c.content.replace("\n", " ").take(48)) {
            currentInputConnection?.commitText(c.content, 1)   // one-tap paste
        }

    private fun saveClipboard() {
        // The IME is the current input method, so reading the clipboard is allowed.
        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val text = cm.primaryClip?.takeIf { it.itemCount > 0 }
            ?.getItemAt(0)?.coerceToText(this)?.toString()
        if (text.isNullOrBlank()) return
        thread {
            Capture.ingest(ClipVaultApp.db(this), text, sourceDevice = android.os.Build.MODEL ?: "android")
            SyncScheduler.requestPush(this)
        }
    }

    private fun button(label: String, onClick: () -> Unit): Button =
        Button(this).apply { text = label; isAllCaps = false; setOnClickListener { onClick() } }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()

    private fun runOnMain(block: () -> Unit) =
        android.os.Handler(mainLooper).post(block)
}
