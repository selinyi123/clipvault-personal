package com.clipvault.app.data

import androidx.room.ColumnInfo
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.RoomDatabase

/** Local cache + outbox (DB-1 subset). The desktop SQLite remains the source
 * of truth; this mirrors only what the phone needs offline. */

@Entity(tableName = "clips", indices = [androidx.room.Index(value = ["contentHash"], unique = true)])
data class ClipEntity(
    @PrimaryKey val id: String,
    val content: String,
    val contentHash: String,
    val contentType: String,
    val isSecret: Boolean,
    val secretLevel: String?,
    val secretReasons: String,            // JSON array as text
    val sourceDevice: String,
    val sourceApp: String?,
    val createdAt: String,
    val lastSeenAt: String,
    val timesSeen: Int,
    val pinned: Boolean = false,
    val favorite: Boolean = false,
    val deleted: Boolean = false,
)

/** One row per locally-originated event awaiting push to the desktop. */
@Entity(tableName = "outbox")
data class OutboxEntity(
    @PrimaryKey(autoGenerate = true) val seq: Long = 0,
    val kind: String,                      // clip_new | clip_meta
    val payload: String,                   // JSON
    val createdAt: String,
)

@Dao
interface ClipDao {
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    fun insert(clip: ClipEntity): Long

    @Query("SELECT * FROM clips WHERE contentHash = :hash LIMIT 1")
    fun byHash(hash: String): ClipEntity?

    @Query("UPDATE clips SET timesSeen = timesSeen + 1, lastSeenAt = :now WHERE id = :id")
    fun touchSeen(id: String, now: String)

    @Query("""SELECT * FROM clips WHERE deleted = 0
              AND (:secret = 1 AND isSecret = 1 OR :secret = 0 AND isSecret = 0)
              AND (:q = '' OR content LIKE '%' || :q || '%')
              ORDER BY pinned DESC, lastSeenAt DESC LIMIT 100""")
    fun list(q: String, secret: Int): List<ClipEntity>

    @Query("UPDATE clips SET deleted = 1 WHERE id = :id")
    fun softDelete(id: String)

    @Query("UPDATE clips SET pinned = :pinned WHERE contentHash = :hash")
    fun setPinnedByHash(hash: String, pinned: Boolean)

    @Query("UPDATE clips SET favorite = :favorite WHERE contentHash = :hash")
    fun setFavoriteByHash(hash: String, favorite: Boolean)
}

@Dao
interface OutboxDao {
    @Insert fun append(e: OutboxEntity): Long

    @Query("SELECT * FROM outbox ORDER BY seq LIMIT :limit")
    fun batch(limit: Int): List<OutboxEntity>

    @Query("DELETE FROM outbox WHERE seq <= :upto")
    fun clearUpTo(upto: Long)
}

/** Personal Memory mirror (S008), synced from the desktop for IME panels. */
@Entity(tableName = "memory", primaryKeys = ["kind", "text"])
data class MemoryEntity(
    val kind: String,
    val text: String,
    val label: String?,
    val pinned: Boolean,
    @ColumnInfo(name = "useCount") val useCount: Int,
    val source: String,
    val deleted: Boolean = false,
)

@Dao
interface MemoryDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    fun upsert(m: MemoryEntity)

    @Query("UPDATE memory SET deleted = 1 WHERE kind = :kind AND text = :text")
    fun softDelete(kind: String, text: String)

    @Query("SELECT * FROM memory WHERE deleted = 0 AND (:kind = '' OR kind = :kind) " +
        "ORDER BY pinned DESC, useCount DESC LIMIT 100")
    fun list(kind: String): List<MemoryEntity>
}

@Database(
    entities = [ClipEntity::class, OutboxEntity::class, MemoryEntity::class],
    version = 1, exportSchema = false,
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun clips(): ClipDao
    abstract fun outbox(): OutboxDao
    abstract fun memory(): MemoryDao
}
