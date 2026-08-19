package com.clipvault.app.ime

import com.clipvault.ime.engine.DirectInputEngineAdapter
import com.clipvault.ime.engine.EngineInputContext
import com.clipvault.ime.engine.EngineFieldKind
import com.clipvault.ime.engine.EngineState
import com.clipvault.ime.engine.EngineTransition
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class EngineSessionControllerTest {
    @Test
    fun keyTravelsThroughEngineBeforeEditorCommit() {
        val editor = RecordingEditor()
        val rendered = mutableListOf<EngineUiState>()
        val controller = EngineSessionController(
            engineFactory = ::DirectInputEngineAdapter,
            editor = editor,
            render = rendered::add,
        )
        controller.begin(ordinaryContext())

        assertTrue(controller.inputText("a"))

        assertEquals(listOf("a"), editor.engineCommits)
        assertTrue(rendered.last().engineAvailable)
    }

    @Test
    fun unavailableEngineFallsBackWithoutRuntimeOrNetwork() {
        val editor = RecordingEditor()
        val controller = EngineSessionController(
            engineFactory = { throw IllegalStateException("native engine unavailable") },
            editor = editor,
            render = {},
        )
        controller.begin(ordinaryContext())

        assertTrue(controller.inputText("x"))
        assertTrue(controller.backspace())

        assertEquals(listOf("x"), editor.directCommits)
        assertEquals(1, editor.directDeletes)
    }

    @Test
    fun ambiguousEditorEffectRetiresWithoutReplayingKey() {
        val editor = RecordingEditor(applyResult = EngineEditorResult.AMBIGUOUS)
        val controller = EngineSessionController(
            engineFactory = ::DirectInputEngineAdapter,
            editor = editor,
            render = {},
        )
        controller.begin(ordinaryContext())

        assertFalse(controller.inputText("a"))
        assertEquals(emptyList<String>(), editor.directCommits)

        assertTrue(controller.inputText("b"))
        assertEquals(listOf("b"), editor.directCommits)
    }

    @Test
    fun finishInvalidatesSessionAndLeavesNoComposition() {
        val editor = RecordingEditor()
        val rendered = mutableListOf<EngineUiState>()
        val controller = EngineSessionController(
            engineFactory = ::DirectInputEngineAdapter,
            editor = editor,
            render = rendered::add,
        )
        controller.begin(ordinaryContext())
        controller.finish()

        assertFalse(rendered.last().engineAvailable)
        assertTrue(controller.inputText("z"))
        assertEquals(listOf("z"), editor.directCommits)
    }

    private fun ordinaryContext() = EngineInputContext(
        fieldKind = EngineFieldKind.TEXT,
        incognito = false,
        learningAllowed = true,
        clipVaultAllowed = true,
    )

    private class RecordingEditor(
        private val applyResult: EngineEditorResult = EngineEditorResult.APPLIED,
    ) : EngineEditor {
        val engineCommits = mutableListOf<String>()
        val directCommits = mutableListOf<String>()
        var directDeletes = 0

        override fun apply(previous: EngineState, transition: EngineTransition): EngineEditorResult {
            transition.commitText?.let(engineCommits::add)
            return applyResult
        }

        override fun commitDirect(text: String): EngineEditorResult {
            directCommits += text
            return EngineEditorResult.APPLIED
        }

        override fun deleteDirect(): EngineEditorResult {
            directDeletes += 1
            return EngineEditorResult.APPLIED
        }

        override fun clearComposition(): EngineEditorResult = EngineEditorResult.APPLIED
    }
}
