package com.clipvault.app.sync

import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.clipvault.app.data.AppDatabase
import com.clipvault.app.data.ClipEntity
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class SyncApplyClipMetaTest {
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
    fun recognizedFieldsAreAppliedTogetherAndCanBeCleared() {
        insertClip(pinned = false, favorite = true, deleted = true)

        applyPatch(
            JSONObject()
                .put("pinned", true)
                .put("favorite", false)
                .put("deleted", false),
        )

        val stored = requireNotNull(db.clips().byHash(HASH))
        assertTrue(stored.pinned)
        assertFalse(stored.favorite)
        assertFalse(stored.deleted)
    }

    @Test
    fun malformedRecognizedFieldCannotPartiallyApplyEarlierField() {
        insertClip(pinned = false, favorite = false, deleted = false)

        applyPatch(
            JSONObject()
                .put("pinned", true)
                .put("favorite", JSONObject.NULL)
                .put("deleted", true),
        )

        val stored = requireNotNull(db.clips().byHash(HASH))
        assertFalse(stored.pinned)
        assertFalse(stored.favorite)
        assertFalse(stored.deleted)
    }

    @Test
    fun unknownFieldsDoNotPreventKnownMetadataFromApplying() {
        insertClip(pinned = false, favorite = false, deleted = false)

        applyPatch(JSONObject().put("favorite", true).put("future_field", JSONObject.NULL))

        val stored = requireNotNull(db.clips().byHash(HASH))
        assertFalse(stored.pinned)
        assertTrue(stored.favorite)
        assertFalse(stored.deleted)
    }

    private fun insertClip(pinned: Boolean, favorite: Boolean, deleted: Boolean) {
        db.clips().insert(
            ClipEntity(
                id = "clip-meta-test",
                content = "safe clip meta test",
                contentHash = HASH,
                contentType = "text",
                isSecret = false,
                secretLevel = null,
                secretReasons = "[]",
                sourceDevice = "desktop",
                sourceApp = null,
                createdAt = "2026-07-14T00:00:00Z",
                lastSeenAt = "2026-07-14T00:00:00Z",
                timesSeen = 1,
                pinned = pinned,
                favorite = favorite,
                deleted = deleted,
            ),
        )
    }

    private fun applyPatch(patch: JSONObject) {
        SyncApply.applyEvents(
            db,
            JSONArray().put(
                JSONObject()
                    .put("kind", "clip_meta")
                    .put(
                        "payload",
                        JSONObject()
                            .put("content_hash", HASH)
                            .put("patch", patch),
                    ),
            ),
        )
    }

    private companion object {
        const val HASH = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    }
}
