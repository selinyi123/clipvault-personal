package com.clipvault.imeapp

import android.os.SystemClock
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.clipvault.app.ime.rime.RimeEngineFactory
import com.clipvault.ime.engine.EngineFieldKind
import com.clipvault.ime.engine.EngineInputContext
import com.clipvault.ime.engine.EngineKeyEvent
import com.clipvault.ime.engine.EngineTransition
import com.clipvault.ime.engine.InputEngineAdapterV2
import com.clipvault.ime.engine.PageDirection
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
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

    @Test
    fun longSentenceAndExplicitApostropheProduceExpectedCandidates() {
        val context = readyContext()
        val inputContext = ordinaryInputContext()
        val engine = RimeEngineFactory.create(context, inputContext)

        var transition = engine.startSession("device-long-sentence", 1, inputContext)
        transition = type(
            engine,
            "device-long-sentence",
            transition,
            "jintianxiawuwomenqukaihui",
            2,
        )
        assertTrue(
            "long-sentence decoder did not produce the expected sentence: ${transition.state.candidates}",
            transition.state.candidates.any { it.text == "今天下午我们去开会" },
        )
        engine.endSession("device-long-sentence", 2L + "jintianxiawuwomenqukaihui".length)

        transition = engine.startSession("device-xian-apostrophe", 1, inputContext)
        transition = type(engine, "device-xian-apostrophe", transition, "xi'an", 2)
        assertTrue(
            "explicit pinyin delimiter did not produce 西安",
            transition.state.candidates.any { it.text == "西安" },
        )
        engine.endSession("device-xian-apostrophe", 2L + "xi'an".length)
    }

    @Test
    fun candidatePaginationMovesBothDirectionsWithoutReusingPageIdentity() {
        val context = readyContext()
        val inputContext = ordinaryInputContext()
        val engine = RimeEngineFactory.create(context, inputContext)
        val sessionId = "device-pagination"
        var transition = engine.startSession(sessionId, 1, inputContext)
        transition = type(engine, sessionId, transition, "shi", 2)
        assertTrue("shi must expose a next candidate page", transition.state.hasNextPage)
        val firstPageIds = transition.state.candidates.map { it.id }

        val next = engine.pageCandidates(
            sessionId,
            requestSequence = 5,
            expectedRevision = transition.state.revision,
            direction = PageDirection.NEXT,
        )
        assertEquals(1, next.state.pageIndex)
        assertTrue(next.state.hasPreviousPage)
        assertNotEquals(firstPageIds, next.state.candidates.map { it.id })

        val previous = engine.pageCandidates(
            sessionId,
            requestSequence = 6,
            expectedRevision = next.state.revision,
            direction = PageDirection.PREVIOUS,
        )
        assertEquals(0, previous.state.pageIndex)
        assertEquals(firstPageIds, previous.state.candidates.map { it.id })
        engine.endSession(sessionId, 7)
    }

    @Test
    fun cancelClearsCompositionAndSameSessionRecoversForNewInput() {
        val context = readyContext()
        val inputContext = ordinaryInputContext()
        val engine = RimeEngineFactory.create(context, inputContext)
        val sessionId = "device-cancel-recovery"
        var transition = engine.startSession(sessionId, 1, inputContext)
        transition = type(engine, sessionId, transition, "nihao", 2)
        assertTrue(transition.state.preedit.isNotEmpty())

        transition = engine.cancelComposition(
            sessionId,
            requestSequence = 7,
            expectedRevision = transition.state.revision,
        )
        assertTrue(transition.state.preedit.isEmpty())
        assertTrue(transition.state.candidates.isEmpty())
        assertNull(transition.commitText)

        transition = type(engine, sessionId, transition, "zhongwen", 8)
        assertTrue(
            "session did not recover after cancellation",
            transition.state.candidates.any { it.text == "中文" },
        )
        val candidate = transition.state.candidates.first { it.text == "中文" }
        val committed = engine.selectCandidate(
            sessionId,
            requestSequence = 16,
            expectedRevision = transition.state.revision,
            candidateId = candidate.id,
        )
        assertEquals("中文", committed.commitText)
        assertTrue(committed.state.preedit.isEmpty())
        engine.endSession(sessionId, 17)
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

    private fun readyContext(): android.content.Context {
        val context = ApplicationProvider.getApplicationContext<android.content.Context>()
        RimeEngineFactory.prewarmAsync(context)
        val deadline = SystemClock.elapsedRealtime() + 30_000L
        while (!RimeEngineFactory.isReady() && SystemClock.elapsedRealtime() < deadline) {
            Thread.sleep(50L)
        }
        assertTrue("Rime background deployment failed", RimeEngineFactory.isReady())
        return context
    }

    private fun ordinaryInputContext() = EngineInputContext(
        fieldKind = EngineFieldKind.TEXT,
        incognito = false,
        learningAllowed = true,
        clipVaultAllowed = true,
    )

    private fun type(
        engine: InputEngineAdapterV2,
        sessionId: String,
        started: EngineTransition,
        text: String,
        firstSequence: Long,
    ): EngineTransition {
        var transition = started
        text.forEachIndexed { index, character ->
            transition = engine.processKey(
                sessionId,
                requestSequence = firstSequence + index,
                expectedRevision = transition.state.revision,
                event = EngineKeyEvent.text(character.toString()),
            )
        }
        return transition
    }
}
