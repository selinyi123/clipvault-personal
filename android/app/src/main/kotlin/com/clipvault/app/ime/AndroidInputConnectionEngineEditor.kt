package com.clipvault.app.ime

import android.view.inputmethod.InputConnection
import com.clipvault.ime.engine.EngineState
import com.clipvault.ime.engine.EngineTransition

/** Applies one validated engine transition to the currently focused editor. */
internal class AndroidInputConnectionEngineEditor(
    private val connectionProvider: () -> InputConnection?,
) : EngineEditor {
    override fun apply(previous: EngineState, transition: EngineTransition): EngineEditorResult {
        val connection = connectionProvider() ?: return EngineEditorResult.REJECTED
        val next = transition.state

        // InputConnection cannot place an internal composing caret without
        // observing surrounding text. Until the native adapter exposes a safe
        // cursor operation, fail closed instead of placing it incorrectly.
        if (next.preedit.isNotEmpty() && next.caretUtf16 != next.preedit.length) {
            return EngineEditorResult.REJECTED
        }

        connection.beginBatchEdit()
        return try {
            val applied = when {
                transition.commitText != null -> {
                    // commitText replaces the active composing range. Finishing
                    // first would commit the raw preedit and then append the
                    // selected candidate (for example, "ni hao你好").
                    connection.commitText(transition.commitText, 1)
                }
                transition.deleteBeforeCodePoints > 0 ->
                    connection.deleteSurroundingTextInCodePoints(
                        transition.deleteBeforeCodePoints,
                        0,
                    )
                previous.preedit != next.preedit ->
                    if (next.preedit.isEmpty()) {
                        connection.setComposingText("", 1) &&
                            connection.finishComposingText()
                    } else {
                        connection.setComposingText(next.preedit, 1)
                    }
                else -> true
            }
            if (applied) EngineEditorResult.APPLIED else EngineEditorResult.REJECTED
        } catch (_: RuntimeException) {
            EngineEditorResult.AMBIGUOUS
        } finally {
            try {
                connection.endBatchEdit()
            } catch (_: RuntimeException) {
                // The primary operation result remains authoritative.
            }
        }
    }

    override fun commitDirect(text: String): EngineEditorResult =
        applyDirect { it.commitText(text, 1) }

    override fun deleteDirect(): EngineEditorResult =
        applyDirect { it.deleteSurroundingTextInCodePoints(1, 0) }

    override fun clearComposition(): EngineEditorResult =
        applyDirect {
            it.setComposingText("", 1) && it.finishComposingText()
        }

    private fun applyDirect(action: (InputConnection) -> Boolean): EngineEditorResult {
        val connection = connectionProvider() ?: return EngineEditorResult.REJECTED
        return try {
            if (action(connection)) EngineEditorResult.APPLIED else EngineEditorResult.REJECTED
        } catch (_: RuntimeException) {
            EngineEditorResult.AMBIGUOUS
        }
    }
}
