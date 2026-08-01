package com.clipvault.ime.engine

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Test

class DirectInputEngineAdapterTest {
    @Test
    fun printableInputCommitsWithoutRetainingComposition() {
        val engine = DirectInputEngineAdapter()
        val start = engine.startSession("session-1", 1, ordinaryContext())

        val result = engine.processKey(
            sessionId = "session-1",
            requestSequence = 2,
            expectedRevision = start.state.revision,
            event = EngineKeyEvent.text("你"),
        )

        assertEquals("你", result.commitText)
        assertEquals("", result.state.preedit)
        assertEquals(1, result.state.revision)
    }

    @Test
    fun backspaceIsAPlatformDeleteOperationNotSyntheticText() {
        val engine = DirectInputEngineAdapter()
        engine.startSession("session-1", 1, ordinaryContext())

        val result = engine.processKey("session-1", 2, 0, EngineKeyEvent.backspace())

        assertNull(result.commitText)
        assertEquals(1, result.deleteBeforeCodePoints)
    }

    @Test
    fun staleRevisionAndSequenceFailClosed() {
        val engine = DirectInputEngineAdapter()
        engine.startSession("session-1", 1, ordinaryContext())

        assertThrows(EngineProtocolException::class.java) {
            engine.processKey("session-1", 3, 0, EngineKeyEvent.text("a"))
        }
        assertThrows(EngineProtocolException::class.java) {
            engine.processKey("session-1", 2, 9, EngineKeyEvent.text("a"))
        }
    }

    @Test
    fun endingSessionInvalidatesIdentityAndWipesState() {
        val engine = DirectInputEngineAdapter()
        engine.startSession("session-1", 1, ordinaryContext())
        engine.processKey("session-1", 2, 0, EngineKeyEvent.text("a"))

        engine.endSession("session-1", 3)

        assertThrows(EngineProtocolException::class.java) {
            engine.processKey("session-1", 3, 1, EngineKeyEvent.text("b"))
        }
        assertEquals(0, engine.startSession("session-2", 1, ordinaryContext()).state.revision)
    }

    @Test
    fun sensitiveContextCannotEnableLearningOrPersonalCandidates() {
        assertThrows(IllegalArgumentException::class.java) {
            EngineInputContext(
                fieldKind = EngineFieldKind.PASSWORD,
                incognito = false,
                learningAllowed = true,
                clipVaultAllowed = false,
            )
        }
        assertThrows(IllegalArgumentException::class.java) {
            EngineInputContext(
                fieldKind = EngineFieldKind.TEXT,
                incognito = true,
                learningAllowed = false,
                clipVaultAllowed = true,
            )
        }
    }

    private fun ordinaryContext() = EngineInputContext(
        fieldKind = EngineFieldKind.TEXT,
        incognito = false,
        learningAllowed = true,
        clipVaultAllowed = true,
    )
}
