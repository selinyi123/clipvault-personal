package com.clipvault.app.data

import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ClipCandidatePageTest {
    private val maxContentBytes = 64 * 1024
    private lateinit var db: AppDatabase

    @Before
    fun openDatabase() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        db = Room.inMemoryDatabaseBuilder(context, AppDatabase::class.java)
            .allowMainThreadQueries()
            .build()
    }

    @After
    fun closeDatabase() {
        db.close()
    }

    @Test
    fun publicCandidateWindowFiltersFlagsAndKeepsDeterministicLimitOrder() {
        insert(id = "a", text = "match a", lastSeenAt = "2026-07-21T00:00:00Z")
        insert(id = "b", text = "match b", lastSeenAt = "2026-07-21T00:00:00Z")
        insert(id = "pinned", text = "match pinned", pinned = true)
        insert(id = "secret", text = "match secret", pinned = true, isSecret = true)
        insert(id = "deleted", text = "match deleted", pinned = true, deleted = true)
        insert(id = "other", text = "different text", lastSeenAt = "2026-07-22T00:00:00Z")

        val firstMeta = db.clips().candidateWindowMetadata(q = "match", secret = 0, limit = 2)
        val fullMeta = db.clips().candidateWindowMetadata(q = "match", secret = 0, limit = 128)
        val first = db.clips().candidateRowsById(firstMeta.map { it.id }, maxContentBytes).associateBy { it.id }
        val full = db.clips().candidateRowsById(fullMeta.map { it.id }, maxContentBytes).associateBy { it.id }

        assertEquals(listOf("pinned", "b"), firstMeta.map { it.id })
        assertEquals(listOf("pinned", "b", "a"), fullMeta.map { it.id })
        assertEquals("match pinned".toByteArray(Charsets.UTF_8).size.toLong(), firstMeta.first().contentBytes)
        assertEquals(listOf("pinned", "b"), firstMeta.mapNotNull { first[it.id]?.id })
        assertEquals(listOf("pinned", "b", "a"), fullMeta.mapNotNull { full[it.id]?.id })
    }

    @Test
    fun payloadReadRechecksStaleFlagsEmptyContentAndUtf8ByteBudget() {
        val exactAscii = safeAscii(maxContentBytes)
        val withinMultibyte = "\u754c".repeat(maxContentBytes / 3)
        val overMultibyte = withinMultibyte + "\u754c"
        insert(id = "stable", text = "ordinary stable row")
        insert(id = "became-oversized", text = "ordinary before metadata")
        insert(id = "became-secret", text = "ordinary before metadata")
        insert(id = "became-deleted", text = "ordinary before metadata")
        insert(id = "became-empty", text = "ordinary before metadata")
        insert(id = "exact-ascii", text = exactAscii)
        insert(id = "within-multibyte", text = withinMultibyte)
        insert(id = "over-multibyte", text = overMultibyte)

        val metadata = db.clips().candidateWindowMetadata(q = "", secret = 0, limit = 128)
        val sql = db.openHelper.writableDatabase
        sql.execSQL(
            "UPDATE clips SET content = ? WHERE id = ?",
            arrayOf(safeAscii(maxContentBytes + 1), "became-oversized"),
        )
        sql.execSQL("UPDATE clips SET isSecret = 1 WHERE id = 'became-secret'")
        sql.execSQL("UPDATE clips SET deleted = 1 WHERE id = 'became-deleted'")
        sql.execSQL("UPDATE clips SET content = '' WHERE id = 'became-empty'")

        val rows = db.clips().candidateRowsById(metadata.map { it.id }, maxContentBytes)

        assertEquals(maxContentBytes, exactAscii.toByteArray(Charsets.UTF_8).size)
        assertEquals(maxContentBytes - 1, withinMultibyte.toByteArray(Charsets.UTF_8).size)
        assertEquals(maxContentBytes + 2, overMultibyte.toByteArray(Charsets.UTF_8).size)
        assertEquals(
            setOf("stable", "exact-ascii", "within-multibyte"),
            rows.map { it.id }.toSet(),
        )
    }

    private fun insert(
        id: String,
        text: String,
        lastSeenAt: String = "2026-07-20T00:00:00Z",
        pinned: Boolean = false,
        isSecret: Boolean = false,
        deleted: Boolean = false,
    ) {
        db.clips().insert(
            ClipEntity(
                id = id,
                content = text,
                contentHash = "hash-$id",
                contentType = "text",
                isSecret = isSecret,
                secretLevel = if (isSecret) "hard" else null,
                secretReasons = if (isSecret) "[\"test\"]" else "[]",
                sourceDevice = "test",
                sourceApp = null,
                createdAt = "2026-07-20T00:00:00Z",
                lastSeenAt = lastSeenAt,
                timesSeen = 1,
                pinned = pinned,
                favorite = false,
                deleted = deleted,
            ),
        )
    }

    private fun safeAscii(length: Int): String =
        "ordinary note ".repeat(length / 14 + 1).take(length)
}
