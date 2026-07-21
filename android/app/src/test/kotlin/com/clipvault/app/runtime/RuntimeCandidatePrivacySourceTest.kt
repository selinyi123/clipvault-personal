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
