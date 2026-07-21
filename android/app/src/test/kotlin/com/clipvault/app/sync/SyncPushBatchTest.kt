package com.clipvault.app.sync

import com.clipvault.app.data.OutboxEntity
import com.clipvault.app.data.OutboxMetadata
import com.clipvault.core.Normalize
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class SyncPushBatchTest {
    private val legacySecret = "AKIAIOSFODNN7EXAMPLE"

    @Test
    fun pushBatchIncludesAllRowsWhenTheyFitTheRequestBudget() {
        val rows = listOf(
            outbox(seq = 1, content = "a"),
            outbox(seq = 2, content = "b"),
        )
        val budget = requestBodyBytesFor(rows, deviceId = "android-test")

        val batch = buildSyncPushBatch(
            batch = rows,
            deviceId = "android-test",
            maxRequestBytes = budget,
        )

        assertEquals(2, batch.events.length())
        assertEquals(2L, batch.maxSeq)
        assertEquals(2, batch.sourceCount)
        assertEquals("a", batch.events.getJSONObject(0).getJSONObject("data").getString("content"))
        assertEquals("b", batch.events.getJSONObject(1).getJSONObject("data").getString("content"))
        assertTrue(requestBodyBytes(batch.events) <= budget)
    }

    @Test
    fun legacyPublicSecretProjectsToContentFreeSameSequenceNoop() {
        val row = outbox(seq = 41, content = legacySecret)

        val batch = buildSyncPushBatch(listOf(row), deviceId = "android-test")

        val event = batch.events.getJSONObject(0)
        assertEquals(41L, event.getLong("seq"))
        assertEquals(PRIVACY_NOOP_KIND, event.getString("kind"))
        assertEquals(PRIVACY_NOOP_TIMESTAMP, event.getString("ts"))
        assertEquals(0, event.getJSONObject("data").length())
        assertEquals(41L, batch.maxSeq)
        assertEquals(1, batch.sourceCount)
        val wire = event.toString()
        assertFalse(wire.contains(legacySecret))
        assertFalse(wire.contains("content_hash"))
        assertFalse(wire.contains("secret_reasons"))
        assertFalse(wire.contains("01J"))
        assertFalse(wire.contains("2026-07-04"))
    }

    @Test
    fun declarativelySecretLegacyRowProjectsToNoopWithoutPayloadMetadata() {
        val original = outbox(seq = 42, content = "safe text")
        val payload = JSONObject(original.payload)
            .put("is_secret", true)
            .put("secret_level", "hard")
            .put("secret_reasons", JSONArray().put("legacy-reason"))
        val row = original.copy(payload = payload.toString())

        val event = buildSyncPushBatch(listOf(row), "android-test")
            .events.getJSONObject(0)

        assertEquals(PRIVACY_NOOP_KIND, event.getString("kind"))
        assertEquals(0, event.getJSONObject("data").length())
        val wire = event.toString()
        assertFalse(wire.contains("safe text"))
        assertFalse(wire.contains("legacy-reason"))
    }

    @Test
    fun safeNoopSafeBatchPreservesOrderAndContinuousSequence() {
        val rows = listOf(
            outbox(seq = 40, content = "safe before"),
            outbox(seq = 41, content = legacySecret),
            outbox(seq = 42, content = "safe after"),
        )

        val batch = buildSyncPushBatch(rows, deviceId = "android-test")

        assertEquals(listOf(40L, 41L, 42L), eventSeqs(batch.events))
        assertEquals(
            listOf("clip_new", PRIVACY_NOOP_KIND, "clip_new"),
            (0 until batch.events.length()).map {
                batch.events.getJSONObject(it).getString("kind")
            },
        )
        assertEquals(42L, batch.maxSeq)
        assertEquals(3, batch.sourceCount)
    }

    @Test
    fun hugeSecretIsBudgetedAsSmallNoopBeforeWireSizeCheck() {
        val content = legacySecret + "\n" +
            "x".repeat(Normalize.DEFAULT_MAX_CLIP_BYTES - legacySecret.length - 1)
        val row = outbox(seq = 43, content = content)

        val batch = buildSyncPushBatch(
            batch = listOf(row),
            deviceId = "android-test",
            maxRequestBytes = 512,
        )

        assertEquals(PRIVACY_NOOP_KIND, batch.events.getJSONObject(0).getString("kind"))
        assertTrue(requestBodyBytes(batch.events) <= 512)
    }

    @Test
    fun malformedOrUnknownRowsRemainBlockedInsteadOfBeingRetired() {
        val valid = outbox(seq = 44, content = "safe structural payload")
        val base = JSONObject(valid.payload)
        val rows = listOf(
            valid.copy(payload = JSONObject(valid.payload).put("extra", true).toString()),
            valid.copy(
                payload = JSONObject(valid.payload).apply { remove("favorite") }.toString(),
            ),
            valid.copy(payload = JSONObject(valid.payload).put("content_hash", "0".repeat(64)).toString()),
            valid.copy(payload = JSONObject(valid.payload).put("is_secret", "false").toString()),
            valid.copy(payload = JSONObject(valid.payload).put("pinned", "false").toString()),
            valid.copy(payload = JSONObject(valid.payload).put("created_at", "2026-02-30T00:00:00Z").toString()),
            valid.copy(payload = JSONObject(valid.payload).put("created_at", "2026-07-04T24:00:00Z").toString()),
            valid.copy(payload = JSONObject(valid.payload).put("created_at", "2026-12-31T23:59:60Z").toString()),
            valid.copy(payload = JSONObject(valid.payload).put("created_at", "0000-01-01T00:00:00Z").toString()),
            valid.copy(createdAt = "2026-07-04T24:00:00Z"),
            valid.copy(kind = "future_event", payload = base.toString()),
        )

        rows.forEach { row ->
            try {
                buildSyncPushBatch(listOf(row), deviceId = "android-test")
                fail("expected INVALID_PAYLOAD for ${row.kind}")
            } catch (e: SyncPushBlockedException) {
                assertEquals(row.seq, e.seq)
                assertEquals(SyncPushBlockReason.INVALID_PAYLOAD, e.reason)
            }
        }
    }

    @Test
    fun declarativelySecretRowsRemainBlockedWhenTheirStructureIsCorrupt() {
        val original = outbox(seq = 47, content = "safe declared secret")
        val declared = JSONObject(original.payload)
            .put("is_secret", true)
            .put("secret_level", "hard")
            .put("secret_reasons", JSONArray().put("legacy-reason"))
        val rows = listOf(
            original.copy(payload = JSONObject(declared.toString()).put("content_hash", "0".repeat(64)).toString()),
            original.copy(payload = JSONObject(declared.toString()).put("created_at", "2026-02-30T00:00:00Z").toString()),
            original.copy(payload = JSONObject(declared.toString()).put("content_type", "future").toString()),
            original.copy(payload = JSONObject(declared.toString()).put("favorite", "false").toString()),
        )

        rows.forEach(::assertInvalidPayload)
    }

    @Test
    fun currentlySecretRowsRemainBlockedWhenTheirStructureIsCorrupt() {
        val currentSecret = outbox(seq = 48, content = legacySecret)
        val normalizedMismatch = outbox(seq = 49, content = "$legacySecret ")
        val rows = listOf(
            currentSecret.copy(
                payload = JSONObject(currentSecret.payload)
                    .put("content_hash", "0".repeat(64))
                    .toString(),
            ),
            normalizedMismatch,
        )

        rows.forEach(::assertInvalidPayload)
    }

    @Test
    fun lenientOrAmbiguousJsonSyntaxRemainsBlocked() {
        val valid = outbox(seq = 50, content = "strict JSON")
        val rows = listOf(
            valid.copy(payload = valid.payload + "TRAILING"),
            valid.copy(payload = valid.payload.dropLast(1) + ",}"),
            valid.copy(payload = valid.payload.replaceFirst("\"id\"", "'id'")),
            valid.copy(payload = valid.payload.replaceFirst("\"id\"", "id")),
            valid.copy(payload = valid.payload.replaceFirst("{", "{\"id\":\"duplicate\",")),
            valid.copy(payload = valid.payload.replaceFirst("{", "{\"\\u0069d\":\"duplicate\",")),
        )

        rows.forEach(::assertInvalidPayload)
    }

    @Test
    fun privacyNoopRowIsClearedOnlyAfterDesktopAcknowledgement() {
        val pending = mutableListOf(outbox(seq = 45, content = legacySecret))
        var observedBeforeAck = false

        val success = drainSyncOutbox(
            nextBatch = { pending.toList() },
            deviceId = "android-test",
            push = { events ->
                observedBeforeAck = pending.single().seq == 45L
                assertEquals(PRIVACY_NOOP_KIND, events.getJSONObject(0).getString("kind"))
                45L
            },
            clearUpTo = { seq -> pending.removeAll { it.seq <= seq } },
        )

        assertTrue(success)
        assertTrue(observedBeforeAck)
        assertTrue(pending.isEmpty())
    }

    @Test
    fun privacyNoopPushFailureLeavesOriginalOutboxRowQueued() {
        val pending = mutableListOf(outbox(seq = 46, content = legacySecret))
        val cleared = mutableListOf<Long>()

        val success = drainSyncOutbox(
            nextBatch = { pending.toList() },
            deviceId = "android-test",
            push = { -1L },
            clearUpTo = { cleared += it },
        )

        assertFalse(success)
        assertTrue(cleared.isEmpty())
        assertEquals(listOf(46L), pending.map { it.seq })
    }

    @Test
    fun pushBatchHonorsRequestBudgetWithoutClearingUnsyncedRows() {
        val rows = listOf(
            outbox(seq = 1, content = "a".repeat(160)),
            outbox(seq = 2, content = "b".repeat(160)),
            outbox(seq = 3, content = "c".repeat(160)),
        )
        val oneEventBytes = requestBodyBytesFor(rows.take(1), deviceId = "android-test")
        val twoEventBytes = requestBodyBytesFor(rows.take(2), deviceId = "android-test")
        val firstOnlyBudget = oneEventBytes + ((twoEventBytes - oneEventBytes) / 2)

        val batch = buildSyncPushBatch(
            batch = rows,
            deviceId = "android-test",
            maxRequestBytes = firstOnlyBudget,
        )

        assertEquals(1, batch.events.length())
        assertEquals(1L, batch.maxSeq)
        assertEquals(1, batch.sourceCount)
        assertEquals(1L, batch.events.getJSONObject(0).getLong("seq"))
        assertTrue(requestBodyBytes(batch.events) <= firstOnlyBudget)
    }

    @Test
    fun maxSizedControlCharacterClipFitsTheProductionRequestBudget() {
        val content = "\u0000".repeat(Normalize.DEFAULT_MAX_CLIP_BYTES)
        assertEquals(Normalize.DEFAULT_MAX_CLIP_BYTES, content.toByteArray(Charsets.UTF_8).size)
        assertNull(Normalize.rejectReason(content))
        val stored = productionClipOutbox(seq = 7, content = content)
        val metadata = OutboxMetadata(
            seq = stored.seq,
            kind = stored.kind,
            createdAt = stored.createdAt,
            payloadChars = stored.payload.codePointCount(0, stored.payload.length).toLong(),
            payloadBytes = stored.payload.toByteArray(Charsets.UTF_8).size.toLong(),
        )
        var chunkCalls = 0
        val rows = loadOutboxBatchFromChunks(listOf(metadata), readChunk = { seq, offset, charCount ->
            assertEquals(7L, seq)
            assertTrue(charCount <= 64 * 1024)
            chunkCalls += 1
            stored.payload.substring(offset - 1, offset - 1 + charCount)
        })
        val wireBytes = requestBodyBytesFor(rows, deviceId = "android-test")

        val batch = buildSyncPushBatch(batch = rows, deviceId = "android-test")

        assertEquals(1, batch.events.length())
        assertEquals(7L, batch.maxSeq)
        assertEquals(wireBytes, requestBodyBytes(batch.events))
        assertTrue(chunkCalls > 1)
        assertEquals(stored.payload, rows.single().payload)
        assertTrue("control-character JSON must exercise the old 4 MiB failure", wireBytes > 4 * 1024 * 1024)
        assertTrue("valid max-size clip must fit the production push budget", wireBytes <= MAX_SYNC_PUSH_REQUEST_BYTES)
    }

    @Test
    fun chunkLoaderRejectsMissingOrShortPayloadWithoutDroppingSequence() {
        val metadata = OutboxMetadata(
            seq = 13,
            kind = "clip_new",
            createdAt = "2026-07-13T00:00:00Z",
            payloadChars = 10,
            payloadBytes = 10,
        )

        try {
            loadOutboxBatchFromChunks(listOf(metadata), readChunk = { _, _, _ -> "short" })
            fail("expected SyncPushBlockedException")
        } catch (e: SyncPushBlockedException) {
            assertEquals(13L, e.seq)
            assertEquals(SyncPushBlockReason.INVALID_PAYLOAD, e.reason)
        }
    }

    @Test
    fun chunkLoaderPreservesEmojiAcrossSqliteCodePointBoundary() {
        val prefix = "{\"content\":\""
        val asciiBeforeEmoji = 64 * 1024 - prefix.codePointCount(0, prefix.length) - 1
        val payload = prefix + "a".repeat(asciiBeforeEmoji) + "😀" +
            "b".repeat(32) + "\",\"content_hash\":\"emoji-boundary\"}"
        val codePoints = payload.codePointCount(0, payload.length)
        val metadata = OutboxMetadata(
            seq = 14,
            kind = "clip_new",
            createdAt = "2026-07-13T00:00:00Z",
            payloadChars = codePoints.toLong(),
            payloadBytes = payload.toByteArray(Charsets.UTF_8).size.toLong(),
        )
        var chunkCalls = 0

        val rows = loadOutboxBatchFromChunks(listOf(metadata), readChunk = { _, offset, charCount ->
            chunkCalls += 1
            val start = payload.offsetByCodePoints(0, offset - 1)
            val available = payload.codePointCount(start, payload.length)
            val end = payload.offsetByCodePoints(start, minOf(charCount, available))
            payload.substring(start, end)
        })

        assertTrue(chunkCalls >= 2)
        assertEquals(payload, rows.single().payload)
        assertTrue(JSONObject(rows.single().payload).getString("content").contains("😀"))
    }

    @Test
    fun pushBatchRejectsAnOversizedFirstEventWithoutSendingIt() {
        val rows = listOf(outbox(seq = 7, content = "oversized"))

        try {
            buildSyncPushBatch(
                batch = rows,
                deviceId = "android-test",
                maxRequestBytes = 1,
            )
            fail("expected SyncPushBlockedException")
        } catch (e: SyncPushBlockedException) {
            assertEquals(7L, e.seq)
            assertEquals(SyncPushBlockReason.EVENT_TOO_LARGE, e.reason)
            assertEquals("sync push event exceeds request budget", e.message)
        }
    }

    @Test
    fun pushBatchRejectsNonPositiveBudget() {
        try {
            buildSyncPushBatch(
                batch = listOf(outbox(seq = 1, content = "x")),
                deviceId = "android-test",
                maxRequestBytes = 0,
            )
            fail("expected IllegalArgumentException")
        } catch (e: IllegalArgumentException) {
            assertEquals("maxRequestBytes must be positive", e.message)
        }
    }

    @Test
    fun drainOutboxSendsMultipleBudgetedPrefixesWithoutClearingUnsentRows() {
        val pending = mutableListOf(
            outbox(seq = 1, content = "a".repeat(160)),
            outbox(seq = 2, content = "b".repeat(160)),
            outbox(seq = 3, content = "c".repeat(160)),
        )
        val oneEventBytes = requestBodyBytesFor(pending.take(1), deviceId = "android-test")
        val twoEventBytes = requestBodyBytesFor(pending.take(2), deviceId = "android-test")
        val firstOnlyBudget = oneEventBytes + ((twoEventBytes - oneEventBytes) / 2)
        val pushedSeqs = mutableListOf<List<Long>>()
        val clearedSeqs = mutableListOf<Long>()

        val success = drainSyncOutbox(
            nextBatch = { pending.toList() },
            deviceId = "android-test",
            push = { events ->
                val seqs = eventSeqs(events)
                pushedSeqs += seqs
                seqs.maxOrNull() ?: -1L
            },
            clearUpTo = { seq ->
                clearedSeqs += seq
                pending.removeAll { it.seq <= seq }
            },
            maxRequestBytes = firstOnlyBudget,
        )

        assertTrue(success)
        assertEquals(listOf(listOf(1L), listOf(2L), listOf(3L)), pushedSeqs)
        assertEquals(listOf(1L, 2L, 3L), clearedSeqs)
        assertTrue(pending.isEmpty())
    }

    @Test
    fun drainOutboxContinuesAcrossSmallRoomReadPages() {
        val pending = (1L..17L).map { outbox(seq = it, content = "row-$it") }.toMutableList()
        val pushedPages = mutableListOf<List<Long>>()

        val success = drainSyncOutbox(
            nextBatch = { pending.take(8) },
            deviceId = "android-test",
            push = { events ->
                val seqs = eventSeqs(events)
                pushedPages += seqs
                seqs.maxOrNull() ?: -1L
            },
            clearUpTo = { seq -> pending.removeAll { it.seq <= seq } },
        )

        assertTrue(success)
        assertEquals(listOf(8, 8, 1), pushedPages.map { it.size })
        assertEquals((1L..17L).toList(), pushedPages.flatten())
        assertTrue(pending.isEmpty())
    }

    @Test
    fun drainOutboxRetriesWhenDesktopDoesNotAckTheFullSentPrefix() {
        val pending = mutableListOf(
            outbox(seq = 1, content = "a"),
            outbox(seq = 2, content = "b"),
        )
        val clearedSeqs = mutableListOf<Long>()

        val success = drainSyncOutbox(
            nextBatch = { pending.toList() },
            deviceId = "android-test",
            push = { 1L },
            clearUpTo = { seq ->
                clearedSeqs += seq
                pending.removeAll { it.seq <= seq }
            },
            maxRequestBytes = requestBodyBytesFor(pending, deviceId = "android-test"),
        )

        assertFalse(success)
        assertEquals(listOf(1L), clearedSeqs)
        assertEquals(listOf(2L), pending.map { it.seq })
    }

    @Test
    fun drainOutboxDoesNotPushOrClearAnOversizedFirstEvent() {
        val pending = listOf(outbox(seq = 9, content = "oversized"))
        var pushCalls = 0
        val clearedSeqs = mutableListOf<Long>()

        try {
            drainSyncOutbox(
                nextBatch = { pending },
                deviceId = "android-test",
                push = {
                    pushCalls += 1
                    9L
                },
                clearUpTo = { clearedSeqs += it },
                maxRequestBytes = 1,
            )
            fail("expected SyncPushBlockedException")
        } catch (e: SyncPushBlockedException) {
            assertEquals(9L, e.seq)
            assertEquals(SyncPushBlockReason.EVENT_TOO_LARGE, e.reason)
            assertEquals("sync push event exceeds request budget", e.message)
        }

        assertEquals(0, pushCalls)
        assertTrue(clearedSeqs.isEmpty())
        assertEquals(listOf(9L), pending.map { it.seq })
    }

    @Test
    fun legacyDesktop413BlocksCurrentPrefixWithoutClearingOrRetryingIt() {
        val pending = listOf(outbox(seq = 10, content = "valid-on-current-desktop"))
        var pushCalls = 0
        val clearedSeqs = mutableListOf<Long>()

        try {
            drainSyncOutbox(
                nextBatch = { pending },
                deviceId = "android-test",
                push = {
                    pushCalls += 1
                    throw SyncPushRequestTooLargeException()
                },
                clearUpTo = { clearedSeqs += it },
            )
            fail("expected SyncPushBlockedException")
        } catch (e: SyncPushBlockedException) {
            assertEquals(10L, e.seq)
            assertEquals(SyncPushBlockReason.EVENT_TOO_LARGE, e.reason)
        }

        assertEquals(1, pushCalls)
        assertTrue(clearedSeqs.isEmpty())
        assertEquals(listOf(10L), pending.map { it.seq })
    }

    @Test
    fun legacyDesktop413SplitsMultiEventPrefixBeforeBlocking() {
        val pending = mutableListOf(
            outbox(seq = 20, content = "first-individually-valid"),
            outbox(seq = 21, content = "second-individually-valid"),
        )
        val attempts = mutableListOf<List<Long>>()
        val clearedSeqs = mutableListOf<Long>()

        val success = drainSyncOutbox(
            nextBatch = { pending.toList() },
            deviceId = "android-test",
            push = { events ->
                val seqs = eventSeqs(events)
                attempts += seqs
                if (seqs.size > 1) throw SyncPushRequestTooLargeException()
                seqs.single()
            },
            clearUpTo = { seq ->
                clearedSeqs += seq
                pending.removeAll { it.seq <= seq }
            },
        )

        assertTrue(success)
        assertEquals(listOf(listOf(20L, 21L), listOf(20L), listOf(21L)), attempts)
        assertEquals(listOf(20L, 21L), clearedSeqs)
        assertTrue(pending.isEmpty())
    }

    @Test
    fun transientPushFailureRetriesWithoutClearingOrBlocking() {
        val pending = listOf(outbox(seq = 22, content = "retry-later"))
        val clearedSeqs = mutableListOf<Long>()

        val success = drainSyncOutbox(
            nextBatch = { pending },
            deviceId = "android-test",
            push = { -1L },
            clearUpTo = { clearedSeqs += it },
        )

        assertFalse(success)
        assertTrue(clearedSeqs.isEmpty())
        assertEquals(listOf(22L), pending.map { it.seq })
    }

    @Test
    fun drainOutboxRejectsAckBeyondTheSentPrefixBeforeClearing() {
        val pending = listOf(
            outbox(seq = 1, content = "a".repeat(160)),
            outbox(seq = 2, content = "b".repeat(160)),
        )
        val firstOnlyBudget = requestBodyBytesFor(pending.take(1), deviceId = "android-test")
        val clearedSeqs = mutableListOf<Long>()

        try {
            drainSyncOutbox(
                nextBatch = { pending },
                deviceId = "android-test",
                push = { 2L },
                clearUpTo = { clearedSeqs += it },
                maxRequestBytes = firstOnlyBudget,
            )
            fail("expected SyncPushBlockedException")
        } catch (e: SyncPushBlockedException) {
            assertEquals(1L, e.seq)
            assertEquals(SyncPushBlockReason.ACK_OUT_OF_RANGE, e.reason)
            assertEquals("sync push acknowledgement exceeds sent prefix", e.message)
        }

        assertTrue(clearedSeqs.isEmpty())
        assertEquals(listOf(1L, 2L), pending.map { it.seq })
    }

    @Test
    fun corruptPayloadFailsPermanentlyBeforeNetworkOrSequenceMutation() {
        val row = OutboxEntity(
            seq = 11,
            kind = "clip_new",
            payload = "not-json",
            createdAt = "2026-07-04T00:00:00Z",
        )

        try {
            buildSyncPushBatch(listOf(row), deviceId = "android-test")
            fail("expected SyncPushBlockedException")
        } catch (e: SyncPushBlockedException) {
            assertEquals(11L, e.seq)
            assertEquals(SyncPushBlockReason.INVALID_PAYLOAD, e.reason)
            assertEquals("sync push event payload is invalid", e.message)
        }
    }

    @Test
    fun drainOutboxSendsValidPrefixThenKeepsTheCorruptSequenceQueued() {
        val pending = mutableListOf(
            outbox(seq = 1, content = "valid"),
            OutboxEntity(
                seq = 2,
                kind = "clip_new",
                payload = "not-json",
                createdAt = "2026-07-04T00:00:00Z",
            ),
        )
        val pushedSeqs = mutableListOf<List<Long>>()
        val clearedSeqs = mutableListOf<Long>()

        try {
            drainSyncOutbox(
                nextBatch = { pending.toList() },
                deviceId = "android-test",
                push = { events ->
                    val seqs = eventSeqs(events)
                    pushedSeqs += seqs
                    seqs.maxOrNull() ?: -1L
                },
                clearUpTo = { seq ->
                    clearedSeqs += seq
                    pending.removeAll { it.seq <= seq }
                },
            )
            fail("expected SyncPushBlockedException")
        } catch (e: SyncPushBlockedException) {
            assertEquals(2L, e.seq)
            assertEquals(SyncPushBlockReason.INVALID_PAYLOAD, e.reason)
            assertEquals("sync push event payload is invalid", e.message)
        }

        assertEquals(listOf(listOf(1L)), pushedSeqs)
        assertEquals(listOf(1L), clearedSeqs)
        assertEquals(listOf(2L), pending.map { it.seq })
    }

    private fun outbox(seq: Long, content: String): OutboxEntity =
        productionClipOutbox(seq, content)

    private fun productionClipOutbox(seq: Long, content: String): OutboxEntity =
        OutboxEntity(
            seq = seq,
            kind = "clip_new",
            payload = JSONObject()
                .put("id", "01J" + seq.toString().padStart(23, '0'))
                .put("content", content)
                .put("content_hash", Normalize.contentHash(content))
                .put("content_type", "text")
                .put("is_secret", false)
                .put("secret_level", JSONObject.NULL)
                .put("secret_reasons", JSONArray())
                .put("source_device", "android-test")
                .put("source_app", JSONObject.NULL)
                .put("created_at", "2026-07-04T00:00:00Z")
                .put("last_seen_at", "2026-07-04T00:00:00Z")
                .put("times_seen", 1)
                .put("pinned", false)
                .put("favorite", false)
                .put("deleted", false)
                .toString(),
            createdAt = "2026-07-04T00:00:00Z",
        )

    private fun requestBodyBytesFor(rows: List<OutboxEntity>, deviceId: String): Int {
        val events = JSONArray()
        rows.forEach { row ->
            events.put(
                JSONObject()
                    .put("origin_device", deviceId)
                    .put("seq", row.seq)
                    .put("kind", row.kind)
                    .put("ts", row.createdAt)
                    .put("data", JSONObject(row.payload)),
            )
        }
        return requestBodyBytes(events)
    }

    private fun requestBodyBytes(events: JSONArray): Int =
        JSONObject()
            .put("events", events)
            .toString()
            .toByteArray(Charsets.UTF_8)
            .size

    private fun eventSeqs(events: JSONArray): List<Long> =
        (0 until events.length()).map { index -> events.getJSONObject(index).getLong("seq") }

    private fun assertInvalidPayload(row: OutboxEntity) {
        try {
            buildSyncPushBatch(listOf(row), deviceId = "android-test")
            fail("expected INVALID_PAYLOAD for sequence ${row.seq}")
        } catch (e: SyncPushBlockedException) {
            assertEquals(row.seq, e.seq)
            assertEquals(SyncPushBlockReason.INVALID_PAYLOAD, e.reason)
        }
    }
}
