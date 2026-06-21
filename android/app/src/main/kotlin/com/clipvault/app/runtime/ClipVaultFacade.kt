package com.clipvault.app.runtime

import android.content.Context
import com.clipvault.app.ClipVaultApp
import com.clipvault.app.capture.Capture
import com.clipvault.app.data.ClipEntity
import com.clipvault.app.data.MemoryEntity
import com.clipvault.app.sync.SyncScheduler

/**
 * ClipVault Runtime API (ADR-0008, ROADMAP_V2 PR2).
 *
 * The single surface that input frontends call to read/write ClipVault content,
 * instead of touching Room directly. Today the Panel IME uses it; later the Full
 * Keyboard Lab and the CandidateMixer use the same facade, so the Runtime stays
 * the one source of truth and the frontends stay thin.
 *
 * Methods are blocking and meant to be called off the main thread (the IME
 * already does its data work on a worker thread). They never throw for empty
 * results — callers get empty lists / false.
 */

/** What a panel or keyboard shows and pastes for a clipboard item. */
data class ClipCandidate(val id: String, val text: String, val contentType: String)

/** What a panel or keyboard shows and pastes for a Personal Memory item. */
data class MemoryCandidate(val id: String, val text: String, val kind: String, val label: String?)

/** Unified candidate consumed by every input frontend. */
data class Candidate(
    val id: String,
    val source: String,        // clip | memory
    val kind: String,          // content_type for clips; memory kind for memory
    val text: String,
    val label: String,
    val score: Int,
    val riskFlags: List<String> = emptyList(),
)

internal object CandidateMixer {
    private val memoryKindWeight = mapOf(
        "phrase" to 42,
        "prompt" to 40,
        "command" to 38,
        "term" to 34,
        "key_info" to 30,
        "path" to 28,
    )

    fun mix(clips: List<ClipEntity>, memories: List<MemoryEntity>, query: String, limit: Int): List<Candidate> {
        val q = query.trim()
        return (clips.map { fromClip(it, q) } + memories.map { fromMemory(it, q) })
            .filter { q.isEmpty() || it.text.contains(q, ignoreCase = true) || it.label.contains(q, ignoreCase = true) }
            .sortedWith(compareByDescending<Candidate> { it.score }
                .thenBy { it.source }
                .thenBy { it.kind }
                .thenBy { it.label }
                .thenBy { it.id })
            .take(limit)
    }

    private fun fromClip(c: ClipEntity, q: String): Candidate {
        val score = 1000 +
            (if (c.pinned) 500 else 0) +
            (if (q.isNotEmpty() && c.content.startsWith(q, ignoreCase = true)) 120 else 0) +
            (if (c.favorite) 80 else 0) +
            c.timesSeen.coerceAtMost(50)
        return Candidate(
            id = c.id,
            source = "clip",
            kind = c.contentType,
            text = c.content,
            label = "[clip:${c.contentType}]",
            score = score,
        )
    }

    private fun fromMemory(m: MemoryEntity, q: String): Candidate {
        val score = 1200 +
            (if (m.pinned) 500 else 0) +
            (if (q.isNotEmpty() && m.text.startsWith(q, ignoreCase = true)) 120 else 0) +
            (memoryKindWeight[m.kind] ?: 20) +
            m.useCount.coerceAtMost(100)
        return Candidate(
            id = "${m.kind}:${m.text}",
            source = "memory",
            kind = m.kind,
            text = m.text,
            label = "[memory:${m.kind}]",
            score = score,
        )
    }
}

interface ClipVaultFacade {
    /** Unified deterministic candidates for all IME frontends. */
    fun listCandidates(query: String = "", limit: Int = 40): List<Candidate>

    /** Recent public clips, newest/pinned first. Secrets are never returned. */
    fun listRecentClips(limit: Int = 40): List<ClipCandidate>

    /** Personal Memory items of one kind (term|phrase|prompt|command|key_info|path). */
    fun listMemory(kind: String, limit: Int = 100): List<MemoryCandidate>

    /** Explicitly save text into the Runtime (e.g. the IME "保存剪贴板" action).
     * Goes through the full capture pipeline (normalize/hash/classify/Secret Guard)
     * and schedules a sync push. Returns true if something was stored. */
    fun saveExplicit(text: String, sourceDevice: String): Boolean
}

/** Default Room-backed implementation.
 *
 * Runtime contract: these methods NEVER throw. A DB error returns an empty
 * list / false instead of crashing the input frontend — for an input method a
 * crash is far worse than a missing panel item (same lesson as the pairing fix).
 */
class RoomClipVaultFacade(context: Context) : ClipVaultFacade {
    private val ctx = context.applicationContext

    override fun listCandidates(query: String, limit: Int): List<Candidate> = safe(emptyList()) {
        val db = ClipVaultApp.db(ctx)
        val clips = db.clips().list(query, 0)        // public only; never secrets
        val memories = db.memory().list("")
        CandidateMixer.mix(clips, memories, query, limit)
    }

    override fun listRecentClips(limit: Int): List<ClipCandidate> = safe(emptyList()) {
        ClipVaultApp.db(ctx).clips().list("", 0)        // public only; never secrets
            .take(limit)
            .map { ClipCandidate(it.id, it.content, it.contentType) }
    }

    override fun listMemory(kind: String, limit: Int): List<MemoryCandidate> = safe(emptyList()) {
        ClipVaultApp.db(ctx).memory().list(kind)
            .take(limit)
            .map { MemoryCandidate("${it.kind}:${it.text}", it.text, it.kind, it.label) }
    }

    override fun saveExplicit(text: String, sourceDevice: String): Boolean = safe(false) {
        if (text.isBlank()) return@safe false
        Capture.ingest(ClipVaultApp.db(ctx), text, sourceDevice = sourceDevice)
        SyncScheduler.requestPush(ctx)
        true
    }

    private inline fun <T> safe(fallback: T, block: () -> T): T =
        try { block() } catch (e: Exception) {
            android.util.Log.w("clipvault.runtime", "facade op failed: ${e.javaClass.simpleName}")
            fallback
        }
}

/** Entry point so frontends don't construct the implementation directly. */
object ClipVaultRuntime {
    fun facade(context: Context): ClipVaultFacade = RoomClipVaultFacade(context)
}
