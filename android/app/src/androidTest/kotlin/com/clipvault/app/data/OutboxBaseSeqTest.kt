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
class OutboxBaseSeqTest {
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
    fun emptyNeverUsedOutboxStartsAtOne() {
        assertEquals(1L, db.outbox().pairingBaseSeq())
    }

    @Test
    fun emptyAcknowledgedOutboxUsesAutoincrementHighWaterMark() {
        val outbox = db.outbox()
        val seq = outbox.append(event("first"))
        assertEquals(1L, seq)

        outbox.clearUpTo(seq)

        assertEquals(2L, db.outbox().pairingBaseSeq())
    }

    @Test
    fun pendingMinimumWinsOverNextAutoincrementSequence() {
        val outbox = db.outbox()
        val first = outbox.append(event("first"))
        val second = outbox.append(event("second"))
        outbox.append(event("third"))
        outbox.clearUpTo(first)

        assertEquals(2L, second)
        assertEquals(second, db.outbox().pairingBaseSeq())
    }

    @Test
    fun exhaustedAutoincrementHighWaterMarkDoesNotOverflow() {
        val outbox = db.outbox()
        val seq = outbox.append(event("seed"))
        outbox.clearUpTo(seq)
        db.openHelper.writableDatabase.execSQL(
            "UPDATE sqlite_sequence SET seq = ? WHERE name = 'outbox'",
            arrayOf<Any>(Long.MAX_VALUE),
        )

        assertEquals(Long.MAX_VALUE, db.outbox().pairingBaseSeq())
    }

    private fun event(payload: String) = OutboxEntity(
        kind = "clip_new",
        payload = payload,
        createdAt = "2026-07-14T00:00:00Z",
    )
}
