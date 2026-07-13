package com.clipvault.app.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files
import java.nio.file.Path

class OutboxBaseSeqSourceTest {
    private val sourcePath = Path.of(
        "src", "main", "kotlin", "com", "clipvault", "app", "data", "Db.kt",
    )

    @Test
    fun pairingBaselineUsesOneReadOnlySqlSnapshotWithoutSchemaChanges() {
        val source = readSource()
        val sqlStart = source.indexOf("internal const val OUTBOX_BASE_SEQ_SQL")
        assertTrue("outbox base query constant is missing", sqlStart >= 0)
        val literalStart = source.indexOf("\"\"\"", sqlStart)
        assertTrue("outbox base query literal is missing", literalStart > sqlStart)
        val literalEnd = source.indexOf("\"\"\"", literalStart + 3)
        assertTrue("outbox base query literal is unterminated", literalEnd > literalStart)
        val query = source.substring(literalStart + 3, literalEnd)

        assertTrue("query must prefer the minimum pending sequence", query.contains("SELECT MIN(seq) FROM outbox"))
        assertTrue("query must preserve the AUTOINCREMENT high-water mark", query.contains("FROM sqlite_sequence"))
        assertTrue("empty acknowledged queues must advance to the next sequence", query.contains("THEN seq + 1"))
        assertTrue("never-used queues must start at one", Regex("""(?m)^\s*1\s*$""").containsMatchIn(query))
        assertTrue("Long overflow must saturate instead of wrapping", query.contains("9223372036854775807"))
        assertFalse("the scalar query must remain a single SQL statement", query.contains(';'))

        val methodStart = source.indexOf("fun pairingBaseSeq(): Long")
        assertTrue("outbox DAO API is missing", methodStart >= 0)
        val methodEnd = source.indexOf("@Query(\"SELECT seq FROM outbox", methodStart)
        assertTrue("outbox DAO boundary marker is missing", methodEnd > methodStart)
        val methodBody = source.substring(methodStart, methodEnd)
        assertEquals(
            "outbox DAO API must issue exactly one SQLite query",
            1,
            Regex("""pairingBaseSeqRaw\(SimpleSQLiteQuery\(OUTBOX_BASE_SEQ_SQL\)\)""")
                .findAll(methodBody)
                .count(),
        )
        assertTrue("DAO API must reject corrupt non-positive results", methodBody.contains("check(baseSeq >= 1L)"))
        assertTrue("sqlite_sequence access must bypass Room schema inference", source.contains("@RawQuery"))
    }

    private fun readSource(): String {
        assertTrue("source file is missing: $sourcePath", Files.isRegularFile(sourcePath))
        return String(Files.readAllBytes(sourcePath), Charsets.UTF_8)
    }
}
