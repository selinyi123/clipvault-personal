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
class MemoryCandidatePageTest {
    private val maxTextBytes = 64 * 1024
    private val maxLabelBytes = 4 * 1024
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
    fun metadataWindowHasNoPayloadAndFiltersKindAndDeletedRows() {
        insert(kind = "term", text = "needle ordinary", useCount = 2, label = "daily")
        insert(kind = "term", text = "needle pinned", pinned = true)
        insert(kind = "term", text = "other text", label = "needle label", useCount = 5)
        insert(kind = "prompt", text = "needle prompt", pinned = true)
        insert(kind = "term", text = "needle deleted", deleted = true, pinned = true)

        val metadata = db.memory().candidateWindowMetadata(kind = "term", limit = 128)
        val hydrated = db.memory().candidateRowsByRowId(
            rowIds = metadata.map { it.rowId },
            kind = "term",
            maxTextBytes = maxTextBytes,
            maxLabelBytes = maxLabelBytes,
        ).associateBy { it.rowId }

        assertEquals(
            listOf("needle pinned", "other text", "needle ordinary"),
            metadata.mapNotNull { hydrated[it.rowId]?.text },
        )
        val ordinaryMeta = metadata.first { hydrated[it.rowId]?.text == "needle ordinary" }
        assertEquals("needle ordinary".toByteArray(Charsets.UTF_8).size.toLong(), ordinaryMeta.textBytes)
        assertEquals("daily".toByteArray(Charsets.UTF_8).size.toLong(), ordinaryMeta.labelBytes)
    }

    @Test
    fun hydrationRechecksStaleKindDeletedAndByteBudgets() {
        val ids = listOf("safe", "changed-kind", "deleted", "large-text", "large-label")
        ids.forEachIndexed { index, id ->
            insert(kind = "term", text = "needle $id", useCount = ids.size - index)
        }
        val metadata = db.memory().candidateWindowMetadata(kind = "term", limit = 128)
        val sql = db.openHelper.writableDatabase
        sql.execSQL("UPDATE memory SET kind = 'prompt' WHERE text = 'needle changed-kind'")
        sql.execSQL("UPDATE memory SET deleted = 1 WHERE text = 'needle deleted'")
        sql.execSQL(
            "UPDATE memory SET text = ? WHERE text = 'needle large-text'",
            arrayOf(safeAscii(maxTextBytes + 1)),
        )
        sql.execSQL(
            "UPDATE memory SET label = ? WHERE text = 'needle large-label'",
            arrayOf(safeAscii(maxLabelBytes + 1)),
        )

        val hydrated = db.memory().candidateRowsByRowId(
            rowIds = metadata.map { it.rowId },
            kind = "term",
            maxTextBytes = maxTextBytes,
            maxLabelBytes = maxLabelBytes,
        )

        assertEquals(setOf("needle safe"), hydrated.map { it.text }.toSet())
    }

    @Test
    fun multibyteTextAndLabelBoundariesUseUtf8Bytes() {
        val withinText = "界".repeat(maxTextBytes / 3)
        val overText = withinText + "界"
        val withinLabel = "界".repeat(maxLabelBytes / 3)
        val overLabel = withinLabel + "界"
        insert(kind = "term", text = withinText, label = withinLabel)
        insert(kind = "term", text = overText, label = "small")
        insert(kind = "term", text = "ordinary over label", label = overLabel)

        val metadata = db.memory().candidateWindowMetadata(kind = "term", limit = 128)
        val hydrated = db.memory().candidateRowsByRowId(
            rowIds = metadata.map { it.rowId },
            kind = "term",
            maxTextBytes = maxTextBytes,
            maxLabelBytes = maxLabelBytes,
        )

        assertEquals(maxTextBytes - 1, withinText.toByteArray(Charsets.UTF_8).size)
        assertEquals(maxTextBytes + 2, overText.toByteArray(Charsets.UTF_8).size)
        assertEquals(maxLabelBytes - 1, withinLabel.toByteArray(Charsets.UTF_8).size)
        assertEquals(maxLabelBytes + 2, overLabel.toByteArray(Charsets.UTF_8).size)
        assertEquals(listOf(withinText), hydrated.map { it.text })
    }

    @Test
    fun validKindsHydrateWithoutMaterializingHugeSourceAndInvalidKindsStayOut() {
        val validKinds = listOf("term", "phrase", "prompt", "command", "key_info", "path")
        val huge = "x".repeat(1_000_000)
        validKinds.forEachIndexed { index, kind ->
            insert(kind = kind, text = "valid $kind", useCount = validKinds.size - index, source = huge)
        }
        insert(kind = "unknown", text = "invalid kind", source = huge)
        insert(kind = huge, text = "overlong kind", source = huge)

        val metadata = db.memory().candidateWindowMetadata(kind = "", limit = 128)
        val hydrated = db.memory().candidateRowsByRowId(
            rowIds = metadata.map { it.rowId },
            kind = "",
            maxTextBytes = maxTextBytes,
            maxLabelBytes = maxLabelBytes,
        )

        assertEquals(validKinds.size, metadata.size)
        assertEquals(validKinds.toSet(), hydrated.map { it.kind }.toSet())
        assertEquals(validKinds.map { "valid $it" }.toSet(), hydrated.map { it.text }.toSet())
    }

    private fun insert(
        kind: String,
        text: String,
        label: String? = null,
        pinned: Boolean = false,
        useCount: Int = 1,
        deleted: Boolean = false,
        source: String = "manual",
    ) {
        db.memory().upsert(
            MemoryEntity(
                kind = kind,
                text = text,
                label = label,
                pinned = pinned,
                useCount = useCount,
                source = source,
                deleted = deleted,
            ),
        )
    }

    private fun safeAscii(length: Int): String =
        "ordinary note ".repeat(length / 14 + 1).take(length)
}
