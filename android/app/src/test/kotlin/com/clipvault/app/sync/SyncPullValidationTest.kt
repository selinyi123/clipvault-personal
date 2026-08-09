package com.clipvault.app.sync

import com.clipvault.core.Normalize
import com.clipvault.core.SECRET_LEVEL_HARD
import com.clipvault.core.SECRET_LEVEL_SUSPECT
import com.clipvault.core.SecretVerdict
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.fail
import org.junit.Test

class SyncPullValidationTest {
    @Test
    fun validDesktopClipEventPassesStrictPullBoundary() {
        assertEquals("clip_new", validatePulledEvent(validClipEvent()))
    }

    @Test
    fun coercibleClipTypesAndHashMismatchAreRejected() {
        val wrongBoolean = validClipEvent().apply {
            getJSONObject("payload").put("pinned", "false")
        }
        assertMalformed(wrongBoolean)

        val wrongHash = validClipEvent().apply {
            getJSONObject("payload").put("content_hash", "0".repeat(64))
        }
        assertMalformed(wrongHash)
    }

    @Test
    fun memoryAndPrivacyEventsUseTheirFrozenWireShapes() {
        val memory = event(
            kind = "memory_upsert",
            payload = JSONObject()
                .put("kind", "phrase")
                .put("text", "safe phrase")
                .put("pinned", false)
                .put("use_count", 0)
                .put("source", "manual"),
        )
        assertEquals("memory_upsert", validatePulledEvent(memory))

        val malformedMemory = JSONObject(memory.toString()).apply {
            getJSONObject("payload").put("use_count", "0")
        }
        assertMalformed(malformedMemory)

        val privacyNoop = event(
            kind = PRIVACY_NOOP_KIND,
            payload = JSONObject(),
            createdAt = PRIVACY_NOOP_TIMESTAMP,
        )
        assertEquals(PRIVACY_NOOP_KIND, validatePulledEvent(privacyNoop))
        privacyNoop.put("created_at", "2026-08-02T00:00:00Z")
        assertMalformed(privacyNoop)
    }

    @Test
    fun unknownKindRemainsForwardCompatibleNoop() {
        assertNull(validatePulledEvent(event("future_kind", JSONObject())))
    }

    @Test
    fun remoteSecretMetadataSurvivesLocalGateAMissAndLocalRulesCanStrengthenIt() {
        val remoteSecret = validClipEvent().apply {
            getJSONObject("payload")
                .put("is_secret", true)
                .put("secret_level", SECRET_LEVEL_HARD)
                .put("secret_reasons", JSONArray().put("REMOTE-CLASSIFIED"))
        }
        assertEquals("clip_new", validatePulledEvent(remoteSecret))

        val preserved = resolvePulledSecretMetadata(
            remoteSecret.getJSONObject("payload"),
            SecretVerdict(isSecret = false, level = null),
        )
        assertEquals(true, preserved.isSecret)
        assertEquals(SECRET_LEVEL_HARD, preserved.level)
        assertEquals(listOf("REMOTE-CLASSIFIED"), preserved.reasons)

        val strengthened = resolvePulledSecretMetadata(
            remoteSecret.getJSONObject("payload"),
            SecretVerdict(
                isSecret = true,
                level = SECRET_LEVEL_SUSPECT,
                reasons = listOf("SG-NEW-RULE"),
            ),
        )
        assertEquals(true, strengthened.isSecret)
        assertEquals(SECRET_LEVEL_SUSPECT, strengthened.level)
        assertEquals(listOf("SG-NEW-RULE"), strengthened.reasons)
    }

    private fun validClipEvent(): JSONObject {
        val content = "ordinary synced text"
        return event(
            kind = "clip_new",
            payload = JSONObject()
                .put("id", "01ARZ3NDEKTSV4RRFFQ69G5FAV")
                .put("content", content)
                .put("content_hash", Normalize.contentHash(content))
                .put("content_type", "text")
                .put("is_secret", false)
                .put("secret_level", JSONObject.NULL)
                .put("secret_reasons", JSONArray())
                .put("source_device", "desktop-main")
                .put("source_app", JSONObject.NULL)
                .put("created_at", "2026-08-02T00:00:00Z")
                .put("last_seen_at", "2026-08-02T00:00:01Z")
                .put("times_seen", 1)
                .put("pinned", false)
                .put("favorite", false)
                .put("deleted", false),
        )
    }

    private fun event(
        kind: String,
        payload: JSONObject,
        createdAt: String = "2026-08-02T00:00:00Z",
    ): JSONObject = JSONObject()
        .put("seq", 1)
        .put("kind", kind)
        .put("payload", payload)
        .put("created_at", createdAt)

    private fun assertMalformed(event: JSONObject) {
        try {
            validatePulledEvent(event)
            fail("expected malformed pull event to be rejected")
        } catch (_: JSONException) {
            // Expected: known malformed events never reach Room.
        }
    }
}
