package com.clipvault.imeapp

import android.os.SystemClock
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.clipvault.app.ime.rime.RimeEngineFactory
import com.clipvault.ime.engine.EngineFieldKind
import com.clipvault.ime.engine.EngineInputContext
import com.clipvault.ime.engine.EngineKeyEvent
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class NativeRimeDeviceTest {
    @Test
    fun backgroundDeployThenHotSessionCommitsNihaoExactlyOnce() {
        val context = ApplicationProvider.getApplicationContext<android.content.Context>()
        RimeEngineFactory.prewarmAsync(context)
        val deadline = SystemClock.elapsedRealtime() + 30_000L
        while (!RimeEngineFactory.isReady() && SystemClock.elapsedRealtime() < deadline) {
            Thread.sleep(50L)
        }
        assertTrue("Rime background deployment failed", RimeEngineFactory.isReady())
        assertTrue(RimeEngineFactory.lastWarmupDurationMs() in 0L..30_000L)

        val inputContext = EngineInputContext(
            fieldKind = EngineFieldKind.TEXT,
            incognito = false,
            learningAllowed = true,
            clipVaultAllowed = true,
        )
        val engine = RimeEngineFactory.create(context, inputContext)
        val startedAt = SystemClock.elapsedRealtime()
        var transition = engine.startSession("device-nihao", 1, inputContext)
        val hotSessionMs = SystemClock.elapsedRealtime() - startedAt
        assertTrue("hot Rime session took ${hotSessionMs}ms", hotSessionMs <= 250L)

        "nihao".forEachIndexed { index, character ->
            transition = engine.processKey(
                sessionId = "device-nihao",
                requestSequence = (index + 2).toLong(),
                expectedRevision = transition.state.revision,
                event = EngineKeyEvent.text(character.toString()),
            )
        }
        assertEquals("你好", transition.state.candidates.first().text)
        val committed = engine.selectCandidate(
            sessionId = "device-nihao",
            requestSequence = 7,
            expectedRevision = transition.state.revision,
            candidateId = transition.state.candidates.first().id,
        )
        assertEquals("你好", committed.commitText)
        assertTrue(committed.state.preedit.isEmpty())
        engine.endSession("device-nihao", 8)
    }

    @Test
    fun normalAndPrivateSchemasCommitChinesePunctuation() {
        val context = ApplicationProvider.getApplicationContext<android.content.Context>()
        RimeEngineFactory.prewarmAsync(context)
        val deadline = SystemClock.elapsedRealtime() + 30_000L
        while (!RimeEngineFactory.isReady() && SystemClock.elapsedRealtime() < deadline) {
            Thread.sleep(50L)
        }
        assertTrue("Rime background deployment failed", RimeEngineFactory.isReady())

        assertPunctuationCommit(
            androidContext = context,
            sessionId = "device-punctuation-normal",
            inputContext = EngineInputContext(
                fieldKind = EngineFieldKind.TEXT,
                incognito = false,
                learningAllowed = true,
                clipVaultAllowed = true,
            ),
            input = ",",
            expected = "，",
        )
        assertPunctuationCommit(
            androidContext = context,
            sessionId = "device-punctuation-private",
            inputContext = EngineInputContext(
                fieldKind = EngineFieldKind.TEXT,
                incognito = true,
                learningAllowed = false,
                clipVaultAllowed = false,
            ),
            input = "?",
            expected = "？",
        )
    }

    @Test
    fun urlFieldKeepsAsciiPunctuation() {
        val context = ApplicationProvider.getApplicationContext<android.content.Context>()
        RimeEngineFactory.prewarmAsync(context)
        val deadline = SystemClock.elapsedRealtime() + 30_000L
        while (!RimeEngineFactory.isReady() && SystemClock.elapsedRealtime() < deadline) {
            Thread.sleep(50L)
        }
        assertTrue("Rime background deployment failed", RimeEngineFactory.isReady())

        assertPunctuationCommit(
            androidContext = context,
            sessionId = "device-url-ascii",
            inputContext = EngineInputContext(
                fieldKind = EngineFieldKind.URL,
                incognito = false,
                learningAllowed = false,
                clipVaultAllowed = false,
            ),
            input = ".",
            expected = ".",
        )
    }

    private fun assertPunctuationCommit(
        androidContext: android.content.Context,
        sessionId: String,
        inputContext: EngineInputContext,
        input: String,
        expected: String,
    ) {
        val engine = RimeEngineFactory.create(androidContext, inputContext)
        val started = engine.startSession(sessionId, 1, inputContext)
        val transition = engine.processKey(
            sessionId = sessionId,
            requestSequence = 2,
            expectedRevision = started.state.revision,
            event = EngineKeyEvent.text(input),
        )
        assertEquals(expected, transition.commitText)
        assertTrue(transition.state.preedit.isEmpty())
        engine.endSession(sessionId, 3)
    }
}
