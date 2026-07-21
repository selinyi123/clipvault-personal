package com.clipvault.app.ime

import android.view.inputmethod.EditorInfo

/**
 * The action advertised by the currently focused editor.
 *
 * This is deliberately a value-only decision: it consumes EditorInfo flags and
 * never reads or retains an InputConnection or any surrounding text.
 */
internal enum class ImeEditorAction(
    val actionId: Int?,
    val keyLabel: String,
    val accessibilityLabel: String,
) {
    NEW_LINE(null, "⏎", "换行"),
    GO(EditorInfo.IME_ACTION_GO, "前往", "前往"),
    SEARCH(EditorInfo.IME_ACTION_SEARCH, "搜索", "搜索"),
    SEND(EditorInfo.IME_ACTION_SEND, "发送", "发送"),
    NEXT(EditorInfo.IME_ACTION_NEXT, "下一项", "下一项"),
    DONE(EditorInfo.IME_ACTION_DONE, "完成", "完成"),
    PREVIOUS(EditorInfo.IME_ACTION_PREVIOUS, "上一项", "上一项"),
    ;

    /**
     * Runs an editor action when available and falls back to a real Enter key
     * whenever the target editor declines it. NEW_LINE always sends Enter.
     */
    fun perform(
        performEditorAction: (Int) -> Boolean,
        sendEnter: () -> Unit,
    ) {
        val handled = actionId?.let(performEditorAction) ?: false
        if (!handled) sendEnter()
    }
}

internal object ImeEditorActionResolver {
    fun resolve(imeOptions: Int): ImeEditorAction {
        if (imeOptions and EditorInfo.IME_FLAG_NO_ENTER_ACTION != 0) {
            return ImeEditorAction.NEW_LINE
        }

        return when (imeOptions and EditorInfo.IME_MASK_ACTION) {
            EditorInfo.IME_ACTION_GO -> ImeEditorAction.GO
            EditorInfo.IME_ACTION_SEARCH -> ImeEditorAction.SEARCH
            EditorInfo.IME_ACTION_SEND -> ImeEditorAction.SEND
            EditorInfo.IME_ACTION_NEXT -> ImeEditorAction.NEXT
            EditorInfo.IME_ACTION_DONE -> ImeEditorAction.DONE
            EditorInfo.IME_ACTION_PREVIOUS -> ImeEditorAction.PREVIOUS
            EditorInfo.IME_ACTION_NONE,
            EditorInfo.IME_ACTION_UNSPECIFIED -> ImeEditorAction.NEW_LINE
            else -> ImeEditorAction.NEW_LINE
        }
    }
}
