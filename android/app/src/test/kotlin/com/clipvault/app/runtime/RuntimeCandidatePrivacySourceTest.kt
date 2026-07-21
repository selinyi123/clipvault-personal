package com.clipvault.app.runtime

import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files
import java.nio.file.Path

class RuntimeCandidatePrivacySourceTest {
    private val source by lazy {
        val path = Path.of(
            "src", "main", "kotlin", "com", "clipvault", "app", "runtime", "ClipVaultFacade.kt",
        )
        assertTrue("Runtime facade source is missing: $path", Files.isRegularFile(path))
        String(Files.readAllBytes(path), Charsets.UTF_8)
    }

    private val dbSource by lazy {
        val path = Path.of(
            "src", "main", "kotlin", "com", "clipvault", "app", "data", "Db.kt",
        )
        assertTrue("Room database source is missing: $path", Files.isRegularFile(path))
        String(Files.readAllBytes(path), Charsets.UTF_8)
    }

    @Test
    fun candidateMixerKeepsPrivacyGateBeforeClipMapping() {
        val body = functionBody("fun mix(")
        val gate = body.indexOf(
            "EligibleClipCandidates.fromRows(clips, EligibleClipCandidates.MAX_ITEMS)",
        )
        val delegated = body.indexOf("return mix(")

        assertTrue("CandidateMixer must apply the current-rule clip gate", gate >= 0)
        assertTrue("raw clips must be wrapped before candidate mapping", delegated >= 0 && gate > delegated)
    }

    @Test
    fun roomCandidateListUsesBoundedWindowBeforeMixerGate() {
        val body = functionBody("override fun listCandidates(")
        val scan = body.indexOf(
            "loadClipCandidateWindow(db, query, EligibleClipCandidates.MAX_ITEMS)",
        )
        val mixer = body.indexOf("CandidateMixer.mix(clips, memories, query, limit)")

        assertTrue("candidate list must use the bounded clip window", scan >= 0)
        assertTrue("bounded clip window must feed the privacy-gated mixer", mixer > scan)
    }

    @Test
    fun rawCandidateMixerRanksTheFullBoundedWindowBeforeOutputLimit() {
        val body = functionBody("fun mix(clips: List<ClipEntity>")
        val gate = body.indexOf(
            "EligibleClipCandidates.fromRows(clips, EligibleClipCandidates.MAX_ITEMS)",
        )

        assertTrue("raw mixer must authorize the complete bounded source before ranking", gate >= 0)
    }

    @Test
    fun rawCandidateMixerCannotBypassMemoryPrivacyAndPayloadGate() {
        val body = functionBody("fun mix(clips: List<ClipEntity>")
        val gate = body.indexOf("EligibleMemoryCandidates.fromRows(")

        assertTrue("raw memories must enter the opaque eligible batch before ranking", gate >= 0)
    }

    @Test
    fun roomRecentClipsKeepsPrivacyGateBeforeTextLeavesRuntime() {
        val body = functionBody("override fun listRecentClips(")
        val scan = body.indexOf("loadClipCandidateWindow(db, \"\", limit)")
        val projection = body.indexOf(".toRecentCandidates(limit)")

        assertTrue("recent clips must use the bounded candidate scan", scan >= 0)
        assertTrue("recent clips must project only the eligible batch", projection > scan)
    }

    @Test
    fun roomLoaderUsesOneMetadataWindowBeforeBoundedFullRowFetches() {
        val body = functionSection(
            "private fun loadClipCandidateWindow(",
            "private inline fun <T> safe(",
        )
        val loader = body.indexOf("EligibleClipCandidates.loadWindow(")
        val metadata = body.indexOf("candidateWindowMetadata(query, 0, limit)")
        val rows = body.indexOf(
            "candidateRowsById(ids, EligibleClipCandidates.MAX_ITEM_UTF8_BYTES)",
        )

        assertTrue("candidate loader must use the opaque bounded-window planner", loader >= 0)
        assertTrue("payload-free metadata must be queried before clip text", metadata > loader)
        assertTrue("full clip rows must only use planner-approved ids", rows > metadata)
    }

    @Test
    fun candidatePayloadDaoRechecksStaleMetadataPrivacyAndByteBudget() {
        assertTrue(dbSource.contains("SELECT * FROM clips WHERE id IN (:ids)"))
        assertTrue(dbSource.contains("AND deleted = 0"))
        assertTrue(dbSource.contains("AND isSecret = 0"))
        assertTrue(
            dbSource.contains(
                "length(CAST(content AS BLOB)) BETWEEN 1 AND :maxContentBytes",
            ),
        )
        assertTrue(
            dbSource.contains(
                "fun candidateRowsById(ids: List<String>, maxContentBytes: Int): List<ClipEntity>",
            ),
        )
    }

    @Test
    fun listCandidatesAndListMemoryShareTheOpaqueMemoryLoader() {
        val candidates = functionBody("override fun listCandidates(")
        val memory = functionBody("override fun listMemory(")

        assertTrue(candidates.contains("loadMemoryCandidateWindow("))
        assertTrue(candidates.contains("CandidateMixer.mix(clips, memories, query, limit)"))
        assertTrue(memory.contains("loadMemoryCandidateWindow("))
        assertTrue(memory.contains(".toMemoryCandidates(limit)"))
        assertTrue(!candidates.contains("db.memory().list("))
        assertTrue(!memory.contains(".memory().list("))
    }

    @Test
    fun roomMemoryLoaderUsesOneMetadataWindowBeforeBoundedHydration() {
        val body = functionSection(
            "private fun loadMemoryCandidateWindow(",
            "private inline fun <T> safe(",
        )
        val gate = body.indexOf("EligibleMemoryCandidates.loadWindow(")
        val metadata = body.indexOf("candidateWindowMetadata(kind = kind, limit = limit)")
        val hydration = body.indexOf("candidateRowsByRowId(")

        assertTrue("memory loader must enter the opaque budget gate", gate >= 0)
        assertTrue("memory metadata must be read before payload rows", metadata > gate)
        assertTrue("only metadata-approved rowids may hydrate payload", hydration > metadata)
    }

    @Test
    fun memoryDaoUsesRowIdMetadataAndRepeatsAllHydrationPredicates() {
        assertTrue(dbSource.contains("SELECT _rowid_ AS rowId,"))
        assertTrue(dbSource.contains("length(CAST(text AS BLOB)) AS textBytes"))
        assertTrue(dbSource.contains("COALESCE(length(CAST(label AS BLOB)), 0) AS labelBytes"))
        assertTrue(dbSource.contains("WHERE _rowid_ IN (:rowIds)"))
        assertTrue(dbSource.contains("AND deleted = 0"))
        assertTrue(
            dbSource.contains(
                "AND kind IN ('term', 'phrase', 'prompt', 'command', 'key_info', 'path')",
            ),
        )
        assertTrue(dbSource.contains("AND (:kind = '' OR kind = :kind)"))
        assertTrue(
            dbSource.contains("length(CAST(text AS BLOB)) BETWEEN 1 AND :maxTextBytes"),
        )
        assertTrue(
            dbSource.contains("COALESCE(length(CAST(label AS BLOB)), 0) <= :maxLabelBytes"),
        )
        assertTrue(!dbSource.contains("fun candidateWindowMetadata(kind: String, q: String"))
        assertTrue(!dbSource.contains("q: String,\n        maxTextBytes"))
        assertTrue(
            Regex(
                "AND kind IN \\('term', 'phrase', 'prompt', 'command', 'key_info', 'path'\\)",
            ).findAll(dbSource).count() == 2,
        )
    }

    @Test
    fun memoryHydrationDoesNotMaterializeStoredSource() {
        val rowStart = dbSource.indexOf("data class MemoryCandidateRow(")
        val rowEnd = dbSource.indexOf("@Dao", rowStart)
        val daoEnd = dbSource.indexOf("@Database", rowEnd)
        assertTrue(rowStart >= 0 && rowEnd > rowStart)
        assertTrue(daoEnd > rowEnd)
        val rowProjection = dbSource.substring(rowStart, rowEnd)
        val memoryDao = dbSource.substring(rowEnd, daoEnd)

        assertTrue(!rowProjection.contains("val source: String"))
        assertTrue(
            dbSource.contains(
                "SELECT _rowid_ AS rowId, kind, text, label, pinned, useCount, deleted",
            ),
        )
        assertTrue(!dbSource.contains("pinned, useCount, source, deleted"))
        assertTrue(rowProjection.contains("source = MEMORY_CANDIDATE_SOURCE_PLACEHOLDER"))
        assertTrue(!memoryDao.contains(":q"))
    }

    private fun functionBody(signature: String): String {
        val start = source.indexOf(signature)
        assertTrue("missing function signature: $signature", start >= 0)
        val open = source.indexOf('{', start)
        assertTrue("missing function body: $signature", open > start)

        var depth = 0
        for (index in open until source.length) {
            when (source[index]) {
                '{' -> depth += 1
                '}' -> {
                    depth -= 1
                    if (depth == 0) return source.substring(open + 1, index)
                }
            }
        }
        throw AssertionError("unterminated function body: $signature")
    }

    private fun functionSection(signature: String, nextSignature: String): String {
        val start = source.indexOf(signature)
        assertTrue("missing function signature: $signature", start >= 0)
        val end = source.indexOf(nextSignature, start + signature.length)
        assertTrue("missing next function signature: $nextSignature", end > start)
        return source.substring(start, end)
    }
}
