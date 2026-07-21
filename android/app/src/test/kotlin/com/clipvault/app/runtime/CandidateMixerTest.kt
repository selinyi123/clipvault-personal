package com.clipvault.app.runtime

import com.clipvault.app.data.ClipEntity
import com.clipvault.app.data.ClipCandidateMetadata
import com.clipvault.app.data.MemoryEntity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class CandidateMixerTest {
    @Test
    fun memoryBeatsRawClipWhenNoQuery() {
        val clips = listOf(clip(id = "clip-1", text = "hello raw clipboard", timesSeen = 20))
        val memories = listOf(memory(kind = "phrase", text = "hello saved phrase", useCount = 1))

        val out = CandidateMixer.mix(clips, memories, query = "", limit = 10)

        assertEquals("memory", out.first().source)
        assertEquals("[memory:phrase]", out.first().label)
    }

    @Test
    fun pinnedClipCanBeatOrdinaryMemory() {
        val clips = listOf(clip(id = "clip-1", text = "deploy command", pinned = true, favorite = true, timesSeen = 50))
        val memories = listOf(memory(kind = "term", text = "deploy", useCount = 1))

        val out = CandidateMixer.mix(clips, memories, query = "", limit = 10)

        assertEquals("clip", out.first().source)
        assertEquals("[clip:command]", out.first().label)
    }

    @Test
    fun queryFiltersAndBoostsPrefixMatches() {
        val clips = listOf(
            clip(id = "clip-1", text = "ssh prod", contentType = "command"),
            clip(id = "clip-2", text = "random note", contentType = "text"),
        )
        val memories = listOf(memory(kind = "phrase", text = "ssh staging"))

        val out = CandidateMixer.mix(clips, memories, query = "ssh", limit = 10)

        assertTrue(out.all { it.text.contains("ssh", ignoreCase = true) || it.label.contains("ssh", ignoreCase = true) })
        assertEquals(listOf("memory", "clip"), out.map { it.source })
    }

    @Test
    fun orderingIsStableForEqualScores() {
        val clips = listOf(
            clip(id = "clip-b", text = "same", contentType = "text"),
            clip(id = "clip-a", text = "same", contentType = "text"),
        )

        val out = CandidateMixer.mix(clips, emptyList(), query = "", limit = 10)

        assertEquals(listOf("clip-a", "clip-b"), out.map { it.id })
    }

    @Test
    fun outputLimitIsAppliedAfterRankingTheFullBoundedClipWindow() {
        val clips = listOf(
            clip(id = "room-first", text = "ordinary first row"),
            clip(
                id = "later-high-score",
                text = "ordinary later row",
                favorite = true,
                timesSeen = 50,
            ),
        )

        val out = CandidateMixer.mix(clips, emptyList(), query = "", limit = 1)

        assertEquals(listOf("later-high-score"), out.map { it.id })
    }

    @Test
    fun outputLimitIsAppliedAfterDeterministicIdTieBreak() {
        val clips = listOf(
            clip(id = "clip-b", text = "ordinary b"),
            clip(id = "clip-a", text = "ordinary a"),
        )

        val out = CandidateMixer.mix(clips, emptyList(), query = "", limit = 1)

        assertEquals(listOf("clip-a"), out.map { it.id })
    }

    @Test
    fun sourceCapKeepsMinoritySourceWhenOneFloods() {
        // v1.6 source caps: a flood of high-score pinned clips must not fully
        // starve memories (or vice versa).
        val clips = (0 until 12).map { clip(id = "clip-$it", text = "clip $it", pinned = true, timesSeen = 50) }
        val memories = (0 until 4).map { memory(kind = "term", text = "mem $it") }

        val out = CandidateMixer.mix(clips, memories, query = "", limit = 8)

        assertEquals(8, out.size)
        assertTrue(out.count { it.source == "memory" } >= 2)  // reserve = max(1, 8 / 4) = 2
        assertTrue(out.count { it.source == "clip" } >= 2)
    }

    @Test
    fun noSourceCapWhenEverythingFits() {
        val clips = listOf(clip(id = "clip-1", text = "deploy", pinned = true, timesSeen = 50))
        val memories = (0 until 3).map { memory(kind = "term", text = "m$it") }

        val out = CandidateMixer.mix(clips, memories, query = "", limit = 10)

        assertEquals(4, out.size)
        assertEquals("clip", out.first().source)  // pinned clip leads; no cap reshuffle
    }

    @Test
    fun legacySecretMemoryNeverBecomesCandidate() {
        val memories = listOf(
            memory(kind = "term", text = "safe term"),
            memory(kind = "term", text = "AKIAIOSFODNN7EXAMPLE"),
        )

        val out = CandidateMixer.mix(emptyList(), memories, query = "", limit = 10)

        assertEquals(listOf("safe term"), out.map { it.text })
    }

    @Test
    fun legacyPublicClipMatchingCurrentSecretGuardNeverBecomesCandidate() {
        val clips = listOf(
            clip(id = "safe", text = "safe recent clipboard"),
            clip(
                id = "legacy-secret",
                text = "AKIAIOSFODNN7EXAMPLE",
                pinned = true,
                favorite = true,
                timesSeen = 50,
            ),
        )

        val out = CandidateMixer.mix(clips, emptyList(), query = "", limit = 10)

        assertEquals(listOf("safe recent clipboard"), out.map { it.text })
    }

    @Test
    fun recentClipPrivacyGateRejectsPersistedAndCurrentRuleSecrets() {
        val clips = listOf(
            clip(id = "safe", text = "safe recent clipboard"),
            clip(id = "persisted-secret", text = "ordinary text", isSecret = true),
            clip(id = "legacy-secret", text = "token=abcdefgh12345678"),
            clip(id = "deleted", text = "deleted text", deleted = true),
        )

        val visible = EligibleClipCandidates.fromRows(clips, desiredCount = 10).rows

        assertEquals(listOf("safe"), visible.map { it.id })
    }

    @Test
    fun boundedWindowCanRefillWithSafeCandidateAfterLegacySecretRows() {
        val rows = (0 until 100).map {
            clip(id = "legacy-secret-$it", text = "AKIAIOSFODNN7EXAMPLE")
        } + clip(id = "safe-after-first-window", text = "safe candidate after legacy rows")
        val rowsById = rows.associateBy { it.id }

        val scanned = EligibleClipCandidates.loadWindow(
            desiredCount = 10,
            fetchMetadata = { limit -> metadataFor(rows.take(limit)) },
            fetchRows = { ids -> ids.mapNotNull(rowsById::get) },
        )
        val out = CandidateMixer.mix(scanned, emptyList(), query = "", limit = 10)

        assertEquals(listOf("safe-after-first-window"), out.map { it.id })
    }

    @Test
    fun candidateWindowQueriesMetadataOnceAndStopsAfterRequestedEligibleItems() {
        val rows = (0 until 200).map { clip(id = "clip-$it", text = "safe candidate $it") }
        val rowsById = rows.associateBy { it.id }
        val metadataLimits = mutableListOf<Int>()
        val materializedBatches = mutableListOf<List<String>>()

        val scanned = EligibleClipCandidates.loadWindow(
            desiredCount = 100,
            fetchMetadata = { limit ->
                metadataLimits += limit
                metadataFor(rows.take(limit))
            },
            fetchRows = { ids ->
                materializedBatches += ids
                ids.mapNotNull(rowsById::get)
            },
        )

        assertEquals(100, scanned.rows.size)
        assertEquals(listOf(EligibleClipCandidates.MAX_ROWS), metadataLimits)
        assertEquals((0 until 100).map { "clip-$it" }, materializedBatches.flatten())
        assertTrue(materializedBatches.all { it.size <= EligibleClipCandidates.MATERIALIZE_BATCH_SIZE })
    }

    @Test
    fun candidateWindowDeduplicatesMetadataBeforeMaterialization() {
        val rows = (0 until 5).map { clip(id = "clip-$it", text = "safe candidate $it") }
        val rowsById = rows.associateBy { it.id }
        val requestedIds = mutableListOf<String>()
        val metadata = metadataFor(rows.take(4)) + metadataFor(listOf(rows.last(), rows.last()))

        val scanned = EligibleClipCandidates.loadWindow(
            desiredCount = 10,
            fetchMetadata = { metadata },
            fetchRows = { ids ->
                requestedIds += ids
                ids.reversed().mapNotNull(rowsById::get)
            },
        )

        assertEquals(5, scanned.rows.size)
        assertEquals(5, scanned.rows.map { it.id }.toSet().size)
        assertEquals((0 until 5).map { "clip-$it" }, requestedIds)
        assertEquals((0 until 5).map { "clip-$it" }, scanned.rows.map { it.id })
    }

    @Test
    fun oversizedMetadataNeverEntersFullRowMaterializationAndBatchesStayBounded() {
        val safeRows = (0 until 7).map { clip(id = "safe-$it", text = "safe candidate $it") }
        val rowsById = safeRows.associateBy { it.id }
        val oversizedId = "oversized"
        val metadata = listOf(
            ClipCandidateMetadata(
                id = oversizedId,
                contentBytes = EligibleClipCandidates.MAX_ITEM_UTF8_BYTES.toLong() + 1,
            ),
        ) + metadataFor(safeRows)
        val materializedBatches = mutableListOf<List<String>>()

        val scanned = EligibleClipCandidates.loadWindow(
            desiredCount = 10,
            fetchMetadata = { metadata },
            fetchRows = { ids ->
                materializedBatches += ids
                ids.mapNotNull(rowsById::get)
            },
        )

        assertEquals(safeRows.map { it.id }, scanned.rows.map { it.id })
        assertTrue(materializedBatches.none { oversizedId in it })
        assertTrue(materializedBatches.all { it.size <= EligibleClipCandidates.MATERIALIZE_BATCH_SIZE })
    }

    @Test
    fun metadataByteBudgetIncludesExactAndMultibyteBoundaryButNeverFetchesOverrun() {
        val exactAscii = safeAscii(EligibleClipCandidates.MAX_ITEM_UTF8_BYTES)
        val withinMultibyte = "\u754c".repeat(EligibleClipCandidates.MAX_ITEM_UTF8_BYTES / 3)
        val overMultibyte = withinMultibyte + "\u754c"
        val rows = listOf(
            clip(id = "exact-ascii", text = exactAscii),
            clip(id = "within-multibyte", text = withinMultibyte),
            clip(id = "over-multibyte", text = overMultibyte),
        )
        val rowsById = rows.associateBy { it.id }
        val materializedIds = mutableListOf<String>()

        val scanned = EligibleClipCandidates.loadWindow(
            desiredCount = 10,
            fetchMetadata = { metadataFor(rows) },
            fetchRows = { ids ->
                materializedIds += ids
                ids.mapNotNull(rowsById::get)
            },
        )

        assertEquals(EligibleClipCandidates.MAX_ITEM_UTF8_BYTES, exactAscii.toByteArray().size)
        assertEquals(EligibleClipCandidates.MAX_ITEM_UTF8_BYTES - 1, withinMultibyte.toByteArray().size)
        assertEquals(EligibleClipCandidates.MAX_ITEM_UTF8_BYTES + 2, overMultibyte.toByteArray().size)
        assertEquals(listOf("exact-ascii", "within-multibyte"), materializedIds)
        assertEquals(listOf("exact-ascii", "within-multibyte"), scanned.rows.map { it.id })
    }

    @Test
    fun staleMetadataPayloadChangesRemainFailClosedInRuntime() {
        val oversized = safeAscii(EligibleClipCandidates.MAX_ITEM_UTF8_BYTES + 1)
        val metadata = listOf(
            "safe",
            "became-empty",
            "became-oversized",
            "became-secret",
            "became-deleted",
        )
            .map { ClipCandidateMetadata(it, contentBytes = 16) }
        val rowsById = listOf(
            clip(id = "safe", text = "ordinary safe row"),
            clip(id = "became-empty", text = ""),
            clip(id = "became-oversized", text = oversized),
            clip(id = "became-secret", text = "ordinary secret row", isSecret = true),
            clip(id = "became-deleted", text = "ordinary deleted row", deleted = true),
        ).associateBy { it.id }

        val scanned = EligibleClipCandidates.loadWindow(
            desiredCount = 10,
            fetchMetadata = { metadata },
            fetchRows = { ids -> ids.mapNotNull(rowsById::get) },
        )

        assertEquals(listOf("safe"), scanned.rows.map { it.id })
    }

    @Test
    fun metadataWindowOverBudgetFailsClosedBeforeMaterialization() {
        val metadata = List(EligibleClipCandidates.MAX_ROWS + 1) {
            ClipCandidateMetadata("clip-$it", contentBytes = 1)
        }
        var fetchedRows = false

        expectIllegalArgument("oversized metadata window must fail closed") {
            EligibleClipCandidates.loadWindow(
                desiredCount = 10,
                fetchMetadata = { metadata },
                fetchRows = {
                    fetchedRows = true
                    emptyList()
                },
            )
        }

        assertTrue(!fetchedRows)
    }

    @Test
    fun unexpectedMaterializedIdFailsClosed() {
        expectIllegalArgument("unexpected row id must fail closed") {
            EligibleClipCandidates.loadWindow(
                desiredCount = 10,
                fetchMetadata = { listOf(ClipCandidateMetadata("requested", contentBytes = 8)) },
                fetchRows = { listOf(clip(id = "unexpected", text = "ordinary")) },
            )
        }
    }

    @Test
    fun duplicateMaterializedIdFailsClosed() {
        val row = clip(id = "requested", text = "ordinary")
        expectIllegalArgument("duplicate row id must fail closed") {
            EligibleClipCandidates.loadWindow(
                desiredCount = 10,
                fetchMetadata = {
                    listOf(
                        ClipCandidateMetadata("requested", contentBytes = 8),
                        ClipCandidateMetadata("also-requested", contentBytes = 8),
                    )
                },
                fetchRows = { listOf(row, row) },
            )
        }
    }

    @Test
    fun currentRuleRescanStopsAtAggregateCharacterBudget() {
        val marker = "AKIAIOSFODNN7EXAMPLE\n"
        val filler = "ordinary note ".repeat(EligibleClipCandidates.MAX_ITEM_CHARS / 14 + 1)
        val secret = marker + filler.take(EligibleClipCandidates.MAX_ITEM_CHARS - marker.length)
        val rows = (0 until EligibleClipCandidates.MAX_ROWS).map {
            clip(id = "secret-$it", text = secret)
        }
        val rowsById = rows.associateBy { it.id }
        val materializedBatches = mutableListOf<List<String>>()

        val scanned = EligibleClipCandidates.loadWindow(
            desiredCount = 10,
            fetchMetadata = { metadataFor(rows) },
            fetchRows = { ids ->
                materializedBatches += ids
                ids.mapNotNull(rowsById::get)
            },
        )

        assertTrue(scanned.rows.isEmpty())
        assertEquals(3, materializedBatches.size)
        assertTrue(materializedBatches.all { it.size <= EligibleClipCandidates.MATERIALIZE_BATCH_SIZE })
    }

    @Test
    fun eligibleBatchRetainsOnlyTheAggregateTextBudget() {
        val largeSafe = "ordinary note ".repeat(EligibleClipCandidates.MAX_ITEM_CHARS / 14)
        val rows = (0 until 10).map { clip(id = "safe-$it", text = largeSafe) }

        val batch = EligibleClipCandidates.fromRows(rows, desiredCount = 10)

        assertTrue(batch.rows.sumOf { it.content.length } <= EligibleClipCandidates.MAX_RETAINED_CHARS)
        assertTrue(batch.rows.size < rows.size)
    }

    @Test
    fun recentProjectionOnlyMapsRowsFromOpaqueEligibleBatch() {
        val batch = EligibleClipCandidates.fromRows(
            listOf(
                clip(id = "safe", text = "safe recent clipboard"),
                clip(id = "secret", text = "AKIAIOSFODNN7EXAMPLE"),
            ),
            desiredCount = 10,
        )

        val recent = batch.toRecentCandidates(limit = 10)

        assertEquals(listOf("safe"), recent.map { it.id })
    }

    private fun metadataFor(rows: List<ClipEntity>): List<ClipCandidateMetadata> = rows.map {
        ClipCandidateMetadata(it.id, it.content.toByteArray(Charsets.UTF_8).size.toLong())
    }

    private fun safeAscii(length: Int): String =
        "ordinary note ".repeat(length / 14 + 1).take(length)

    private fun expectIllegalArgument(message: String, block: () -> Unit) {
        try {
            block()
            fail(message)
        } catch (_: IllegalArgumentException) {
            // expected
        }
    }

    private fun clip(
        id: String,
        text: String,
        contentType: String = "command",
        pinned: Boolean = false,
        favorite: Boolean = false,
        timesSeen: Int = 1,
        isSecret: Boolean = false,
        deleted: Boolean = false,
    ) = ClipEntity(
        id = id,
        content = text,
        contentHash = "hash-$id",
        contentType = contentType,
        isSecret = isSecret,
        secretLevel = null,
        secretReasons = "[]",
        sourceDevice = "test",
        sourceApp = null,
        createdAt = "2026-06-21T00:00:00Z",
        lastSeenAt = "2026-06-21T00:00:00Z",
        timesSeen = timesSeen,
        pinned = pinned,
        favorite = favorite,
        deleted = deleted,
    )

    private fun memory(
        kind: String,
        text: String,
        pinned: Boolean = false,
        useCount: Int = 1,
    ) = MemoryEntity(
        kind = kind,
        text = text,
        label = null,
        pinned = pinned,
        useCount = useCount,
        source = "manual",
        deleted = false,
    )
}
