package com.clipvault.app.runtime

import android.content.Context
import com.clipvault.app.ClipVaultApp
import com.clipvault.app.capture.Capture
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

interface ClipVaultFacade {
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
