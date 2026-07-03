package com.clipvault.app.ime

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ImePrivacySessionTest {
    @Test
    fun startsFailClosedBeforeAnyEditor() {
        assertFalse(ImePrivacySession().allowsPersonalData())
    }

    @Test
    fun sensitiveEditorBlocksCurrentPersonalDataActions() {
        val session = ImePrivacySession()
        val token = session.begin(suppressPersonalData = true)

        assertTrue(session.isCurrent(token))
        assertFalse(session.allowsPersonalData(token))
    }

    @Test
    fun editorTransitionInvalidatesInFlightCandidateResult() {
        val session = ImePrivacySession()
        val ordinary = session.begin(suppressPersonalData = false)
        assertTrue(session.allowsPersonalData(ordinary))

        val sensitive = session.begin(suppressPersonalData = true)

        assertFalse(session.isCurrent(ordinary))
        assertFalse(session.allowsPersonalData(ordinary))
        assertFalse(session.allowsPersonalData(sensitive))
    }

    @Test
    fun endingInputInvalidatesInFlightCandidateResult() {
        val session = ImePrivacySession()
        val ordinary = session.begin(suppressPersonalData = false)

        session.end()

        assertFalse(session.isCurrent(ordinary))
        assertFalse(session.allowsPersonalData())
    }

    @Test
    fun sensitiveTransitionInvalidatesInFlightExplicitSaveAction() {
        val session = ImePrivacySession()
        val saveActionToken = session.begin(suppressPersonalData = false)

        session.begin(suppressPersonalData = true)

        assertFalse(session.isCurrent(saveActionToken))
        assertFalse(session.allowsPersonalData(saveActionToken))
    }

    @Test
    fun returningToOrdinaryEditorUsesOnlyNewGeneration() {
        val session = ImePrivacySession()
        val first = session.begin(suppressPersonalData = false)
        session.begin(suppressPersonalData = true)
        val second = session.begin(suppressPersonalData = false)

        assertFalse(session.allowsPersonalData(first))
        assertTrue(session.allowsPersonalData(second))
    }
}
