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

/** Payload-free first pass for the Runtime candidate window. Large clip text
 * is materialized only after this projection passes the IME item budget. */
data class ClipCandidateMetadata(
    val id: String,
    val contentBytes: Long,
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

    /** One payload-free bounded window. Persisted flags are only a prefilter;
     * current Secret Guard rules remain a separate Runtime exit gate. */
    @Query("""SELECT id, length(CAST(content AS BLOB)) AS contentBytes
              FROM clips WHERE deleted = 0
              AND (:secret = 1 AND isSecret = 1 OR :secret = 0 AND isSecret = 0)
              AND (:q = '' OR content LIKE '%' || :q || '%')
              ORDER BY pinned DESC, lastSeenAt DESC, id DESC
              LIMIT :limit""")
    fun candidateWindowMetadata(q: String, secret: Int, limit: Int): List<ClipCandidateMetadata>

    /** Materialize only rows that still satisfy the persisted privacy and
     * payload budgets. Metadata and payload reads are separate statements, so
     * every predicate must be repeated here to close that race window. Runtime
     * still rechecks these fields and current Secret Guard rules afterwards. */
    @Query("""SELECT * FROM clips WHERE id IN (:ids)
              AND deleted = 0
              AND isSecret = 0
              AND length(CAST(content AS BLOB)) BETWEEN 1 AND :maxContentBytes""")
    fun candidateRowsById(ids: List<String>, maxContentBytes: Int): List<ClipEntity>

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

/** Payload-free first pass for Personal Memory candidates. The composite
 * primary key includes the full text, so SQLite's stable-for-the-row `_rowid_`
 * is used only as a short-lived hydration key between the two bounded reads. */
data class MemoryCandidateMetadata(
    val rowId: Long,
    val textBytes: Long,
    val labelBytes: Long,
)

internal const val MEMORY_CANDIDATE_SOURCE_PLACEHOLDER = "candidate_projection"

/** Bounded hydration projection which retains the rowid used to request it.
 * Runtime revalidates every field before converting this to [MemoryEntity].
 * The candidate path never consumes `source`, so the unbounded stored value is
 * deliberately not hydrated; [toEntity] uses a non-persisted fixed marker. */
data class MemoryCandidateRow(
    val rowId: Long,
    val kind: String,
    val text: String,
    val label: String?,
    val pinned: Boolean,
    @ColumnInfo(name = "useCount") val useCount: Int,
    val deleted: Boolean,
) {
    internal fun toEntity(): MemoryEntity = MemoryEntity(
        kind = kind,
        text = text,
        label = label,
        pinned = pinned,
        useCount = useCount,
        source = MEMORY_CANDIDATE_SOURCE_PLACEHOLDER,
        deleted = deleted,
    )
}

@Dao
interface MemoryDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    fun upsert(m: MemoryEntity)

    @Query("UPDATE memory SET deleted = 1 WHERE kind = :kind AND text = :text")
    fun softDelete(kind: String, text: String)

    @Query("SELECT * FROM memory WHERE deleted = 0 AND (:kind = '' OR kind = :kind) " +
        "ORDER BY pinned DESC, useCount DESC LIMIT 100")
    fun list(kind: String): List<MemoryEntity>

    /** One fixed payload-free window. Kind eligibility is repeated by the
     * hydration query because another writer can change the row in between.
     * Query matching stays in Runtime to preserve Kotlin ignore-case behavior. */
    @Query(
        """SELECT _rowid_ AS rowId,
                  length(CAST(text AS BLOB)) AS textBytes,
                  COALESCE(length(CAST(label AS BLOB)), 0) AS labelBytes
             FROM memory
            WHERE deleted = 0
              AND kind IN ('term', 'phrase', 'prompt', 'command', 'key_info', 'path')
              AND (:kind = '' OR kind = :kind)
            ORDER BY pinned DESC, useCount DESC, kind ASC, _rowid_ ASC
            LIMIT :limit""",
    )
    fun candidateWindowMetadata(kind: String, limit: Int): List<MemoryCandidateMetadata>

    /** Hydrate only metadata-approved rowids and repeat all eligibility and
     * byte-size predicates to close the race between the two Room statements. */
    @Query(
        """SELECT _rowid_ AS rowId, kind, text, label, pinned, useCount, deleted
             FROM memory
            WHERE _rowid_ IN (:rowIds)
              AND deleted = 0
              AND kind IN ('term', 'phrase', 'prompt', 'command', 'key_info', 'path')
              AND (:kind = '' OR kind = :kind)
              AND length(CAST(text AS BLOB)) BETWEEN 1 AND :maxTextBytes
              AND COALESCE(length(CAST(label AS BLOB)), 0) <= :maxLabelBytes""",
    )
    fun candidateRowsByRowId(
        rowIds: List<Long>,
        kind: String,
        maxTextBytes: Int,
        maxLabelBytes: Int,
    ): List<MemoryCandidateRow>
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
