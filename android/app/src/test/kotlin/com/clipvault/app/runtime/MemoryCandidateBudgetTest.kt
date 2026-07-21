package com.clipvault.app.runtime

import com.clipvault.app.data.MemoryCandidateMetadata
import com.clipvault.app.data.MemoryCandidateRow
import com.clipvault.app.data.MemoryEntity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class MemoryCandidateBudgetTest {
    @Test
    fun fixedMetadataWindowHydratesInSmallBatchesAndKeepsAtMostOneHundred() {
        val rows = (0 until 140).map { row(it.toLong() + 1L, text = "safe memory $it") }
        val rowsById = rows.associateBy { it.rowId }
        val metadataLimits = mutableListOf<Int>()
        val hydratedBatches = mutableListOf<List<Long>>()

        val eligible = EligibleMemoryCandidates.loadWindow(
            desiredCount = 140,
            query = "",
            kind = "",
            fetchMetadata = { limit ->
                metadataLimits += limit
                rows.take(limit).map(::metadataFor)
            },
            fetchRows = { rowIds ->
                hydratedBatches += rowIds
                rowIds.reversed().mapNotNull(rowsById::get)
            },
        )

        assertEquals(listOf(EligibleMemoryCandidates.MAX_ROWS), metadataLimits)
        assertEquals(EligibleMemoryCandidates.MAX_ITEMS, eligible.rows.size)
        assertEquals((0 until 100).map { "safe memory $it" }, eligible.rows.map { it.text })
        assertTrue(hydratedBatches.all { it.size <= EligibleMemoryCandidates.MATERIALIZE_BATCH_SIZE })
        assertEquals((1L..100L).toList(), hydratedBatches.flatten())
    }

    @Test
    fun metadataRejectsOversizedTextAndLabelBeforeHydration() {
        val safe = row(3, text = "safe", label = "label")
        val requested = mutableListOf<Long>()
        val eligible = EligibleMemoryCandidates.loadWindow(
            desiredCount = 10,
            query = "",
            kind = "",
            fetchMetadata = {
                listOf(
                    MemoryCandidateMetadata(
                        rowId = 1,
                        textBytes = EligibleMemoryCandidates.MAX_TEXT_UTF8_BYTES.toLong() + 1L,
                        labelBytes = 0,
                    ),
                    MemoryCandidateMetadata(
                        rowId = 2,
                        textBytes = 4,
                        labelBytes = EligibleMemoryCandidates.MAX_LABEL_UTF8_BYTES.toLong() + 1L,
                    ),
                    metadataFor(safe),
                )
            },
            fetchRows = { rowIds ->
                requested += rowIds
                listOf(safe)
            },
        )

        assertEquals(listOf(3L), requested)
        assertEquals(listOf("safe"), eligible.rows.map { it.text })
    }

    @Test
    fun hydrationRechecksDeletedKindQuerySizeAndCurrentSecretRules() {
        val hugeText = safeAscii(EligibleMemoryCandidates.MAX_TEXT_CHARS + 1)
        val hugeLabel = safeAscii(EligibleMemoryCandidates.MAX_LABEL_CHARS + 1)
        val rows = listOf(
            row(1, text = "needle safe"),
            row(2, text = "needle wrong kind", kind = "prompt"),
            row(3, text = "does not match"),
            row(4, text = "needle deleted", deleted = true),
            row(5, text = "needle AKIAIOSFODNN7EXAMPLE"),
            row(6, text = "needle label secret", label = "AKIAIOSFODNN7EXAMPLE"),
            row(7, text = hugeText),
            row(8, text = "needle huge label", label = hugeLabel),
        )
        val rowsById = rows.associateBy { it.rowId }

        val eligible = EligibleMemoryCandidates.loadWindow(
            desiredCount = 10,
            query = "needle",
            kind = "term",
            fetchMetadata = {
                rows.map { MemoryCandidateMetadata(it.rowId, textBytes = 16, labelBytes = 0) }
            },
            fetchRows = { rowIds -> rowIds.reversed().mapNotNull(rowsById::get) },
        )

        assertEquals(listOf("needle safe"), eligible.rows.map { it.text })
    }

    @Test
    fun currentRuleScanStopsAtAggregateByteBudget() {
        val marker = "AKIAIOSFODNN7EXAMPLE\n"
        val text = safeAscii(60 * 1024)
        val secretLabel = marker + safeAscii(
            EligibleMemoryCandidates.MAX_LABEL_UTF8_BYTES - marker.toByteArray().size,
        )
        val rows = (1L..20L).map { row(it, text = text, label = secretLabel) }
        val rowsById = rows.associateBy { it.rowId }
        val hydratedBatches = mutableListOf<List<Long>>()

        val eligible = EligibleMemoryCandidates.loadWindow(
            desiredCount = 10,
            query = "",
            kind = "",
            fetchMetadata = { rows.map(::metadataFor) },
            fetchRows = { rowIds ->
                hydratedBatches += rowIds
                rowIds.mapNotNull(rowsById::get)
            },
        )

        assertTrue(eligible.rows.isEmpty())
        assertEquals(2, hydratedBatches.size)
        assertTrue(hydratedBatches.all { it.size <= EligibleMemoryCandidates.MATERIALIZE_BATCH_SIZE })
    }

    @Test
    fun retainedPayloadUsesUtf8BytesAndStopsAtAggregateBudget() {
        val label = safeAscii(EligibleMemoryCandidates.MAX_LABEL_UTF8_BYTES)
        val rows = (1L..10L).map { rowId ->
            val prefix = "row-$rowId "
            row(
                rowId,
                text = prefix + safeAscii(60 * 1024 - prefix.length),
                label = label,
            )
        }

        val eligible = EligibleMemoryCandidates.fromRows(
            rows = rows.map(MemoryCandidateRow::toEntity),
            desiredCount = 10,
        )

        assertEquals(4, eligible.rows.size)
        assertTrue(
            eligible.rows.sumOf {
                it.text.toByteArray(Charsets.UTF_8).size +
                    (it.label?.toByteArray(Charsets.UTF_8)?.size ?: 0)
            } <=
                EligibleMemoryCandidates.MAX_RETAINED_BYTES,
        )
    }

    @Test
    fun extremeLegacyStringsFailClosedBeforeUtf8Materialization() {
        val extreme = "x".repeat(1_000_000)
        val eligible = EligibleMemoryCandidates.fromRows(
            rows = listOf(
                memory(text = extreme),
                memory(text = "safe", label = extreme),
                memory(text = "ordinary retained"),
            ),
            desiredCount = 10,
        )

        assertEquals(listOf("ordinary retained"), eligible.rows.map { it.text })
    }

    @Test
    fun opaqueBatchProjectsOnlyAuthorizedRowsForListMemory() {
        val eligible = EligibleMemoryCandidates.fromRows(
            rows = listOf(
                memory(text = "safe phrase", kind = "phrase"),
                memory(text = "AKIAIOSFODNN7EXAMPLE", kind = "phrase"),
            ),
            desiredCount = 10,
            kind = "phrase",
        )

        val projected = eligible.toMemoryCandidates(limit = 10)

        assertEquals(listOf("phrase:safe phrase"), projected.map { it.id })
    }

    @Test
    fun generatedMemoryLabelPreservesLegacyQueryMatching() {
        for (query in listOf("memory", "term", "memory:term")) {
            val out = CandidateMixer.mix(
                clips = emptyList(),
                memories = listOf(memory(text = "ordinary command", kind = "term", label = "unrelated")),
                query = query,
                limit = 10,
            )

            assertEquals("query=$query", listOf("ordinary command"), out.map { it.text })
        }
    }

    @Test
    fun storedLabelAloneDoesNotExpandCandidateQuerySemantics() {
        val out = CandidateMixer.mix(
            clips = emptyList(),
            memories = listOf(memory(text = "ordinary command", kind = "term", label = "private alias")),
            query = "private alias",
            limit = 10,
        )

        assertTrue(out.isEmpty())
    }

    @Test
    fun nonAsciiQueryKeepsKotlinIgnoreCaseSemanticsAfterFixedMetadataWindow() {
        val out = CandidateMixer.mix(
            clips = emptyList(),
            memories = listOf(memory(text = "Äpfel Notiz", kind = "phrase")),
            query = "äPFEL",
            limit = 10,
        )

        assertEquals(listOf("Äpfel Notiz"), out.map { it.text })
    }

    @Test
    fun invalidAndOverlongKindsFailClosedWhileStoredSourceIsDiscarded() {
        val huge = "x".repeat(1_000_000)
        val eligible = EligibleMemoryCandidates.fromRows(
            rows = listOf(
                memory(text = "valid", kind = "term", source = huge),
                memory(text = "invalid", kind = "unknown"),
                memory(text = "overlong", kind = huge),
            ),
            desiredCount = 10,
        )

        assertEquals(listOf("valid"), eligible.rows.map { it.text })
        assertEquals(listOf("candidate_projection"), eligible.rows.map { it.source })
    }

    @Test
    fun overlongRequestedKindFailsBeforeMetadataRead() {
        var metadataRead = false
        val eligible = EligibleMemoryCandidates.loadWindow(
            desiredCount = 10,
            query = "",
            kind = "x".repeat(1_000_000),
            fetchMetadata = {
                metadataRead = true
                emptyList()
            },
            fetchRows = { emptyList() },
        )

        assertTrue(eligible.rows.isEmpty())
        assertTrue(!metadataRead)
    }

    @Test
    fun oversizedMetadataWindowFailsClosedBeforeHydration() {
        var hydrated = false
        expectIllegalArgument {
            EligibleMemoryCandidates.loadWindow(
                desiredCount = 10,
                query = "",
                kind = "",
                fetchMetadata = {
                    List(EligibleMemoryCandidates.MAX_ROWS + 1) {
                        MemoryCandidateMetadata(it.toLong() + 1L, textBytes = 1, labelBytes = 0)
                    }
                },
                fetchRows = {
                    hydrated = true
                    emptyList()
                },
            )
        }
        assertTrue(!hydrated)
    }

    private fun metadataFor(row: MemoryCandidateRow): MemoryCandidateMetadata = MemoryCandidateMetadata(
        rowId = row.rowId,
        textBytes = row.text.toByteArray(Charsets.UTF_8).size.toLong(),
        labelBytes = row.label?.toByteArray(Charsets.UTF_8)?.size?.toLong() ?: 0L,
    )

    private fun row(
        rowId: Long,
        text: String,
        kind: String = "term",
        label: String? = null,
        deleted: Boolean = false,
    ): MemoryCandidateRow = MemoryCandidateRow(
        rowId = rowId,
        kind = kind,
        text = text,
        label = label,
        pinned = false,
        useCount = 1,
        deleted = deleted,
    )

    private fun memory(
        text: String,
        kind: String = "term",
        label: String? = null,
        source: String = "manual",
    ): MemoryEntity = MemoryEntity(
        kind = kind,
        text = text,
        label = label,
        pinned = false,
        useCount = 1,
        source = source,
        deleted = false,
    )

    private fun safeAscii(length: Int): String =
        "ordinary note ".repeat(length / 14 + 1).take(length)

    private fun expectIllegalArgument(block: () -> Unit) {
        try {
            block()
            fail("expected IllegalArgumentException")
        } catch (_: IllegalArgumentException) {
            // expected
        }
    }
}
