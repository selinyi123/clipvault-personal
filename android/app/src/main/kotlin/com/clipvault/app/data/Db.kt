package com.clipvault.app.data

import androidx.room.ColumnInfo
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.RawQuery
import androidx.room.RoomDatabase
import androidx.sqlite.db.SimpleSQLiteQuery
import androidx.sqlite.db.SupportSQLiteQuery

/**
 * Return the first durable Android outbox sequence that a newly paired desktop
 * must still accept.  When the pending queue is empty, sqlite_sequence keeps
 * the AUTOINCREMENT high-water mark after acknowledged rows have been deleted.
 *
 * Keep this as one SQLite statement: reading MIN(seq) and sqlite_sequence in
 * separate statements would let a concurrent capture land between snapshots
 * and make pairing skip that new event.  Saturating at Long.MAX_VALUE avoids
 * SQLite promoting `seq + 1` to REAL when the sequence space is exhausted; a
 * later append will fail rather than wrap or reuse an acknowledged sequence.
 */
internal const val OUTBOX_BASE_SEQ_SQL = """
SELECT COALESCE(
    (SELECT MIN(seq) FROM outbox),
    (
        SELECT CASE
            WHEN seq < 9223372036854775807 THEN seq + 1
            ELSE 9223372036854775807
        END
        FROM sqlite_sequence
        WHERE name = 'outbox'
    ),
    1
)
"""

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

/** Payload-free outbox projection. Large JSON is read separately in bounded
 * SQLite substr() chunks so API 26/27 CursorWindow never has to hold one full
 * escaped payload cell. */
data class OutboxMetadata(
    val seq: Long,
    val kind: String,
    val createdAt: String,
    val payloadChars: Long,
    val payloadBytes: Long,
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

    /** Apply a remote metadata patch as one SQLite statement. Nullable values
     * mean "field absent", which is distinct from an explicit false value. */
    @Query(
        """UPDATE clips SET
              pinned = CASE WHEN :pinned IS NULL THEN pinned ELSE :pinned END,
              favorite = CASE WHEN :favorite IS NULL THEN favorite ELSE :favorite END,
              deleted = CASE WHEN :deleted IS NULL THEN deleted ELSE :deleted END
           WHERE contentHash = :hash""",
    )
    fun applyMetaPatch(
        hash: String,
        pinned: Boolean?,
        favorite: Boolean?,
        deleted: Boolean?,
    ): Int
}

@Dao
interface OutboxDao {
    @Insert fun append(e: OutboxEntity): Long

    /**
     * Pairing cursor baseline for this durable outbox stream.
     *
     * sqlite_sequence is an SQLite-owned table rather than a Room entity, so
     * the fixed scalar query enters Room through @RawQuery. The `Raw` suffix
     * makes that generated adapter boundary explicit; production callers use
     * only the fixed no-argument wrapper below.
     */
    @RawQuery
    fun pairingBaseSeqRaw(query: SupportSQLiteQuery): Long

    fun pairingBaseSeq(): Long {
        val baseSeq = pairingBaseSeqRaw(SimpleSQLiteQuery(OUTBOX_BASE_SEQ_SQL))
        check(baseSeq >= 1L) { "outbox base sequence is invalid" }
        return baseSeq
    }

    @Query("SELECT seq FROM outbox ORDER BY seq LIMIT 1")
    fun firstSeq(): Long?

    @Query(
        "SELECT seq, kind, createdAt, length(payload) AS payloadChars, " +
            "length(CAST(payload AS BLOB)) AS payloadBytes " +
            "FROM outbox ORDER BY seq LIMIT :limit",
    )
    fun batchMetadata(limit: Int): List<OutboxMetadata>

    @Query("SELECT substr(payload, :offset, :charCount) FROM outbox WHERE seq = :seq")
    fun payloadChunk(seq: Long, offset: Int, charCount: Int): String?

    @Query("SELECT COUNT(*) FROM outbox")
    fun count(): Int

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
