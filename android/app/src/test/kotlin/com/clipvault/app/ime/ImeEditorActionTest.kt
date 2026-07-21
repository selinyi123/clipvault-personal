package com.clipvault.app.ime

import android.view.inputmethod.EditorInfo
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ImeEditorActionTest {
    @Test
    fun resolvesEverySupportedStandardEditorAction() {
        val expected = mapOf(
            EditorInfo.IME_ACTION_GO to ImeEditorAction.GO,
            EditorInfo.IME_ACTION_SEARCH to ImeEditorAction.SEARCH,
            EditorInfo.IME_ACTION_SEND to ImeEditorAction.SEND,
            EditorInfo.IME_ACTION_NEXT to ImeEditorAction.NEXT,
            EditorInfo.IME_ACTION_DONE to ImeEditorAction.DONE,
            EditorInfo.IME_ACTION_PREVIOUS to ImeEditorAction.PREVIOUS,
        )

        expected.forEach { (imeOptions, action) ->
            assertEquals(action, ImeEditorActionResolver.resolve(imeOptions))
            assertEquals(imeOptions, action.actionId)
            assertTrue(action.keyLabel.isNotBlank())
            assertTrue(action.accessibilityLabel.isNotBlank())
        }
    }

    @Test
    fun noneUnspecifiedAndUnknownActionsResolveToNewLine() {
        assertEquals(
            ImeEditorAction.NEW_LINE,
            ImeEditorActionResolver.resolve(EditorInfo.IME_ACTION_NONE),
        )
        assertEquals(
            ImeEditorAction.NEW_LINE,
            ImeEditorActionResolver.resolve(EditorInfo.IME_ACTION_UNSPECIFIED),
        )
        assertEquals(ImeEditorAction.NEW_LINE, ImeEditorActionResolver.resolve(0x7f))
    }

    @Test
    fun resolverMasksNonActionFlags() {
        val options = EditorInfo.IME_ACTION_SEND or EditorInfo.IME_FLAG_NO_EXTRACT_UI

        assertEquals(ImeEditorAction.SEND, ImeEditorActionResolver.resolve(options))
    }

    @Test
    fun noEnterActionFlagAlwaysRequestsNewLine() {
        val options = EditorInfo.IME_ACTION_SEARCH or EditorInfo.IME_FLAG_NO_ENTER_ACTION

        assertEquals(ImeEditorAction.NEW_LINE, ImeEditorActionResolver.resolve(options))
    }

    @Test
    fun handledEditorActionDoesNotSendEnter() {
        val performed = mutableListOf<Int>()
        var enterSent = false

        ImeEditorAction.SEARCH.perform(
            performEditorAction = { actionId -> performed += actionId; true },
            sendEnter = { enterSent = true },
        )

        assertEquals(listOf(EditorInfo.IME_ACTION_SEARCH), performed)
        assertFalse(enterSent)
    }

    @Test
    fun declinedEditorActionFallsBackToEnter() {
        val performed = mutableListOf<Int>()
        var enterCount = 0

        ImeEditorAction.DONE.perform(
            performEditorAction = { actionId -> performed += actionId; false },
            sendEnter = { enterCount += 1 },
        )

        assertEquals(listOf(EditorInfo.IME_ACTION_DONE), performed)
        assertEquals(1, enterCount)
    }

    @Test
    fun newLineSkipsEditorActionAndSendsEnter() {
        var performed = false
        var enterCount = 0

        ImeEditorAction.NEW_LINE.perform(
            performEditorAction = { performed = true; true },
            sendEnter = { enterCount += 1 },
        )

        assertFalse(performed)
        assertEquals(1, enterCount)
    }
}
