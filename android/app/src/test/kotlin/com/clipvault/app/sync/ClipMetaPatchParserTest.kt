package com.clipvault.app.sync

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class ClipMetaPatchParserTest {
    @Test
    fun parserDistinguishesMissingFieldsFromExplicitFalse() {
        val parsed = parseClipMetaPatch(
            JSONObject()
                .put("pinned", false)
                .put("deleted", false),
        )

        assertEquals(false, parsed.pinned)
        assertNull(parsed.favorite)
        assertEquals(false, parsed.deleted)
        assertFalse(parsed.isEmpty)
    }

    @Test
    fun parserAcceptsRecognizedBooleansAndIgnoresUnknownFields() {
        val parsed = parseClipMetaPatch(
            JSONObject()
                .put("pinned", true)
                .put("future_field", "forward-compatible"),
        )

        assertEquals(true, parsed.pinned)
        assertNull(parsed.favorite)
        assertNull(parsed.deleted)
    }

    @Test
    fun parserTreatsUnknownOnlyPatchAsEmpty() {
        val parsed = parseClipMetaPatch(JSONObject().put("future_field", true))

        assertTrue(parsed.isEmpty)
    }

    @Test
    fun parserRejectsJsonNullAndNonBooleanKnownFields() {
        val invalidValues = listOf(JSONObject.NULL, "false", 0, 1)

        for (value in invalidValues) {
            try {
                parseClipMetaPatch(JSONObject().put("favorite", value))
                fail("expected malformed favorite=$value to be rejected")
            } catch (_: org.json.JSONException) {
                // Expected: known fields are never coerced.
            }
        }
    }

    @Test
    fun parserValidatesAllRecognizedFieldsBeforeReturning() {
        try {
            parseClipMetaPatch(
                JSONObject()
                    .put("pinned", true)
                    .put("favorite", JSONObject.NULL),
            )
            fail("expected malformed favorite to reject the complete patch")
        } catch (_: org.json.JSONException) {
            // No partially parsed patch escapes to the database layer.
        }
    }
}
