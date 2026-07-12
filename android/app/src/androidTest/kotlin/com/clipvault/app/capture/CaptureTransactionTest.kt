package com.clipvault.app.capture

import android.content.Context
import android.database.SQLException
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.filters.SdkSuppress
import com.clipvault.app.data.AppDatabase
import com.clipvault.app.sync.MAX_SYNC_PUSH_REQUEST_BYTES
import com.clipvault.app.sync.buildSyncPushBatch
import com.clipvault.app.sync.loadOutboxBatchFromChunks
import com.clipvault.core.Normalize
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.util.concurrent.Callable
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

@RunWith(AndroidJUnit4::class)
class CaptureTransactionTest {
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
    fun outboxFailureRollsBackThePublicClipInsert() {
        db.openHelper.writableDatabase.execSQL(
            """
            CREATE TRIGGER reject_test_outbox
            BEFORE INSERT ON outbox
            BEGIN
                SELECT RAISE(ABORT, 'forced outbox failure');
            END
            """.trimIndent(),
        )

        val raw = "transaction rollback probe"
        try {
            Capture.ingest(db, raw, sourceDevice = "instrumented-test")
            fail("forced outbox failure should escape Capture.ingest")
        } catch (_: SQLException) {
            // Expected: the enclosing Room transaction must roll back the clip.
        }

        val hash = Normalize.contentHash(Normalize.normalize(raw))
        assertNull(db.clips().byHash(hash))
        assertEquals(0, db.outbox().count())
    }

    @Test
    fun concurrentDuplicateCaptureCreatesOneClipAndOneOutboxEvent() {
        val start = CountDownLatch(1)
        val executor = Executors.newFixedThreadPool(2)
        try {
            val calls = (1..2).map {
                executor.submit(Callable {
                    start.await()
                    Capture.ingest(db, "same concurrent content", sourceDevice = "instrumented-test")
                })
            }
            start.countDown()
            val results = calls.map { it.get(10, TimeUnit.SECONDS) }

            assertEquals(1, results.count { it.status == Capture.Status.NEW })
            assertEquals(1, results.count { it.status == Capture.Status.DUPLICATE })
            assertEquals(1, results.mapNotNull { it.clip?.id }.distinct().size)
            assertEquals(1, db.outbox().count())

            val hash = Normalize.contentHash(Normalize.normalize("same concurrent content"))
            val stored = db.clips().byHash(hash)
            assertEquals(2, stored?.timesSeen)
            assertEquals(stored?.id, results.single { it.status == Capture.Status.DUPLICATE }.clip?.id)
        } finally {
            executor.shutdownNow()
        }
    }

    @Test
    @SdkSuppress(minSdkVersion = 26, maxSdkVersion = 27)
    fun maxControlCharacterCaptureCanBeReadThroughBoundedOutboxChunks() {
        val content = "\u0000".repeat(Normalize.DEFAULT_MAX_CLIP_BYTES)
        val result = Capture.ingest(db, content, sourceDevice = "instrumented-test")
        assertEquals(Capture.Status.NEW, result.status)

        val outbox = db.outbox()
        val metadata = outbox.batchMetadata(8)
        assertEquals(1, metadata.size)
        assertTrue(metadata.single().payloadBytes > 4L * 1024 * 1024)

        val rows = loadOutboxBatchFromChunks(metadata, outbox::payloadChunk)
        val batch = buildSyncPushBatch(rows, deviceId = "android-test")
        val wireBytes = JSONObject()
            .put("events", batch.events)
            .toString()
            .toByteArray(Charsets.UTF_8)
            .size

        assertEquals(1, batch.events.length())
        assertEquals(content, batch.events.getJSONObject(0).getJSONObject("data").getString("content"))
        assertTrue(wireBytes <= MAX_SYNC_PUSH_REQUEST_BYTES)
    }
}
