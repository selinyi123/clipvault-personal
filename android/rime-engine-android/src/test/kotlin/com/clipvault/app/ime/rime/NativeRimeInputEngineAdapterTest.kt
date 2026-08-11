package com.clipvault.app.ime.rime

import com.clipvault.ime.engine.EngineFieldKind
import com.clipvault.ime.engine.EngineInputContext
import com.clipvault.ime.engine.EngineKeyEvent
import com.clipvault.ime.engine.EngineProtocolException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeRimeInputEngineAdapterTest {
    @Test
    fun `nihao candidate selection commits once and reset clears composition`() {
        val api = FakeRimeApi()
        val engine = NativeRimeInputEngineAdapter(api)
        val context = EngineInputContext(EngineFieldKind.TEXT, false, true, true)
        var transition = engine.startSession("android-test", 1, context)

        "nihao".forEachIndexed { index, character ->
            transition = engine.processKey(
                "android-test",
                (index + 2).toLong(),
                transition.state.revision,
                EngineKeyEvent.text(character.toString()),
            )
        }

        assertEquals("ni hao", transition.state.preedit)
        assertEquals("你好", transition.state.candidates.first().text)
        val candidateId = transition.state.candidates.first().id
        assertTrue(candidateId.matches(Regex("rime:[0-9a-f-]{36}")))
        assertFalse(
            "candidate identity must not encode page or array position",
            candidateId.contains(":0:0"),
        )
        val selected = engine.selectCandidate(
            "android-test",
            7,
            transition.state.revision,
            transition.state.candidates.first().id,
        )
        assertEquals("你好", selected.commitText)
        assertTrue(selected.state.preedit.isEmpty())

        val composing = engine.processKey(
            "android-test",
            8,
            selected.state.revision,
            EngineKeyEvent.text("n"),
        )
        val reset = engine.cancelComposition(
            "android-test",
            9,
            composing.state.revision,
        )
        assertTrue(reset.state.preedit.isEmpty())
        engine.endSession("android-test", 10)
        assertEquals("clipvault_pinyin", api.selectedSchema)
    }

    @Test
    fun `incognito session selects no-learning schema and still composes`() {
        val api = FakeRimeApi()
        val engine = NativeRimeInputEngineAdapter(api)
        val context = EngineInputContext(EngineFieldKind.TEXT, true, false, false)

        var transition = engine.startSession("private-test", 1, context)
        "nihao".forEachIndexed { index, character ->
            transition = engine.processKey(
                "private-test",
                (index + 2).toLong(),
                transition.state.revision,
                EngineKeyEvent.text(character.toString()),
            )
        }

        assertEquals("clipvault_pinyin_private", api.selectedSchema)
        assertEquals("ni hao", transition.state.preedit)
    }

    @Test
    fun `unhandled emoji commits live composition then emoji without stale native state`() {
        val api = FakeRimeApi()
        val engine = NativeRimeInputEngineAdapter(api)
        val context = EngineInputContext(EngineFieldKind.TEXT, false, true, true)
        var transition = engine.startSession("emoji-test", 1, context)
        transition = engine.processKey(
            "emoji-test",
            2,
            transition.state.revision,
            EngineKeyEvent.text("n"),
        )

        val committed = engine.processKey(
            "emoji-test",
            3,
            transition.state.revision,
            EngineKeyEvent.text("😀"),
        )

        assertEquals("n😀", committed.commitText)
        assertTrue(committed.state.preedit.isEmpty())
        assertEquals("", api.input)
    }

    @Test
    fun `specialized fields start Rime in ASCII mode`() {
        listOf(
            EngineFieldKind.NUMBER,
            EngineFieldKind.PHONE,
            EngineFieldKind.EMAIL,
            EngineFieldKind.URL,
        ).forEach { fieldKind ->
            val api = FakeRimeApi()
            val engine = NativeRimeInputEngineAdapter(api)
            val context = EngineInputContext(fieldKind, false, true, true)

            engine.startSession("ascii-$fieldKind", 1, context)

            assertEquals(true, api.options["ascii_mode"])
            engine.endSession("ascii-$fieldKind", 2)
        }
    }

    @Test
    fun `candidate identity survives a page round trip within one composition`() {
        val api = PagedFakeRimeApi()
        val engine = NativeRimeInputEngineAdapter(api)
        val context = EngineInputContext(EngineFieldKind.TEXT, false, true, true)
        var transition = engine.startSession("paging-test", 1, context)
        transition = engine.processKey(
            "paging-test",
            2,
            transition.state.revision,
            EngineKeyEvent.text("s"),
        )
        val firstPageId = transition.state.candidates.first().id

        transition = engine.pageCandidates(
            "paging-test",
            3,
            transition.state.revision,
            com.clipvault.ime.engine.PageDirection.NEXT,
        )
        val secondPageId = transition.state.candidates.first().id
        assertNotEquals(firstPageId, secondPageId)

        transition = engine.pageCandidates(
            "paging-test",
            4,
            transition.state.revision,
            com.clipvault.ime.engine.PageDirection.PREVIOUS,
        )
        assertEquals(firstPageId, transition.state.candidates.first().id)
        engine.endSession("paging-test", 5)
    }

    @Test
    fun `malformed native page metadata fails closed and destroys the session`() {
        val api = FakeRimeApi().apply { malformedLastPage = true }
        val engine = NativeRimeInputEngineAdapter(api)
        val context = EngineInputContext(EngineFieldKind.TEXT, false, true, true)

        try {
            engine.startSession("malformed-test", 1, context)
            throw AssertionError("expected malformed snapshot rejection")
        } catch (_: EngineProtocolException) {
            assertTrue(api.destroyed)
        }
    }

    @Test
    fun `negative native caret fails closed and destroys the session`() {
        val api = FakeRimeApi().apply { negativeCaret = true }
        val engine = NativeRimeInputEngineAdapter(api)
        val context = EngineInputContext(EngineFieldKind.TEXT, false, true, true)

        try {
            engine.startSession("negative-caret-test", 1, context)
            throw AssertionError("expected negative caret rejection")
        } catch (_: EngineProtocolException) {
            assertTrue(api.destroyed)
        }
    }

    @Test
    fun `native caret beyond preedit fails closed and destroys the session`() {
        val api = FakeRimeApi().apply { oversizedCaret = true }
        val engine = NativeRimeInputEngineAdapter(api)
        val context = EngineInputContext(EngineFieldKind.TEXT, false, true, true)

        try {
            engine.startSession("oversized-caret-test", 1, context)
            throw AssertionError("expected oversized caret rejection")
        } catch (_: EngineProtocolException) {
            assertTrue(api.destroyed)
        }
    }

    @Test
    fun `empty native preedit with nonzero caret fails closed and destroys the session`() {
        val api = FakeRimeApi().apply { malformedEmptyPreeditCaret = true }
        val engine = NativeRimeInputEngineAdapter(api)
        val context = EngineInputContext(EngineFieldKind.TEXT, false, true, true)

        try {
            engine.startSession("empty-preedit-malformed-test", 1, context)
            throw AssertionError("expected empty-preedit caret rejection")
        } catch (_: EngineProtocolException) {
            assertTrue(api.destroyed)
        }
    }

    private class FakeRimeApi : RimeNativeApi {
        var input = ""
        var malformedLastPage = false
        var negativeCaret = false
        var oversizedCaret = false
        var malformedEmptyPreeditCaret = false
        var destroyed = false
        private var commit: String? = null
        var selectedSchema: String? = null
        val options = mutableMapOf<String, Boolean>()

        override fun initialize(sharedDir: String, userDir: String) = true
        override fun createSession() = 1L
        override fun selectSchema(session: Long, schemaId: String): Boolean {
            selectedSchema = schemaId
            return true
        }
        override fun setOption(session: Long, option: String, enabled: Boolean) {
            options[option] = enabled
        }
        override fun processKey(session: Long, keycode: Int, mask: Int): Boolean {
            if (keycode > 0x7f) return false
            if (keycode == 0xff08) input = input.dropLast(1) else input += keycode.toChar()
            return true
        }
        override fun snapshot(session: Long): Array<String> =
            if (malformedEmptyPreeditCaret) {
                arrayOf("", "1", "0", "1")
            } else if (negativeCaret) {
                arrayOf("ni", "-1", "0", "1")
            } else if (oversizedCaret) {
                arrayOf("ni", "99", "0", "1")
            } else if (malformedLastPage) {
                arrayOf("ni", "2", "0", "2")
            } else if (input == "nihao") {
                arrayOf("ni hao", "6", "0", "1", "你好", "")
            } else {
                arrayOf(input, input.length.toString(), "0", "1")
            }
        override fun takeCommit(session: Long): String? = commit.also { commit = null }
        override fun selectCandidate(session: Long, indexOnPage: Int): Boolean {
            if (input == "nihao" && indexOnPage == 0) {
                input = ""
                commit = "你好"
                return true
            }
            return false
        }
        override fun commitComposition(session: Long): Boolean {
            if (input.isEmpty()) return false
            commit = if (input == "nihao") "你好" else input
            input = ""
            return true
        }
        override fun clearComposition(session: Long) { input = "" }
        override fun destroySession(session: Long) {
            destroyed = true
            input = ""
        }
    }

    private class PagedFakeRimeApi : RimeNativeApi {
        var input = ""
        var page = 0

        override fun initialize(sharedDir: String, userDir: String) = true
        override fun createSession() = 1L
        override fun selectSchema(session: Long, schemaId: String) = true
        override fun setOption(session: Long, option: String, enabled: Boolean) = Unit
        override fun processKey(session: Long, keycode: Int, mask: Int): Boolean {
            when (keycode) {
                0xff56 -> page = 1
                0xff55 -> page = 0
                else -> if (keycode in 0..0x7f) input += keycode.toChar() else return false
            }
            return true
        }
        override fun snapshot(session: Long): Array<String> = when {
            input != "s" -> arrayOf(input, input.length.toString(), page.toString(), "1")
            page == 0 -> arrayOf("s", "1", "0", "0", "世", "")
            else -> arrayOf("s", "1", "1", "1", "是", "")
        }
        override fun takeCommit(session: Long): String? = null
        override fun selectCandidate(session: Long, indexOnPage: Int) = false
        override fun commitComposition(session: Long) = false
        override fun clearComposition(session: Long) { input = "" }
        override fun destroySession(session: Long) { input = "" }
    }
}
