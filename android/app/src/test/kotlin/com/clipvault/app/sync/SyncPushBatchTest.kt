package com.clipvault.app.sync

import com.clipvault.app.data.OutboxEntity
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class SyncPushBatchTest {
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
    fun pushBatchIncludesAtLeastOneEventEvenWhenTheBudgetIsTooSmall() {
        val rows = listOf(outbox(seq = 7, content = "oversized"))

        val batch = buildSyncPushBatch(
            batch = rows,
            deviceId = "android-test",
            maxRequestBytes = 1,
        )

        assertEquals(1, batch.events.length())
        assertEquals(7L, batch.maxSeq)
        assertEquals(1, batch.sourceCount)
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

    private fun outbox(seq: Long, content: String): OutboxEntity =
        OutboxEntity(
            seq = seq,
            kind = "clip_new",
            payload = JSONObject()
                .put("content", content)
                .put("content_hash", "hash-$seq")
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
}
