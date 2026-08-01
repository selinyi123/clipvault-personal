package com.clipvault.poc.rime

import java.io.Closeable
import java.io.File

/**
 * Thread-confined, in-memory facade for the isolated librime PoC.
 *
 * The caller owns both data directories. A test case must provide a fresh,
 * disposable user-data directory and delete it after closing this object.
 */
class RimeNativeEngine private constructor(
    private var nativeHandle: Long,
    private val ownerThreadId: Long,
) : Closeable {

    data class Candidate(
        val text: String,
        val comment: String,
    )

    data class Snapshot(
        val composition: String,
        val highlightedCandidateIndex: Int,
        val candidates: List<Candidate>,
    )

    val engineVersion: String
        get() = withHandle { nativeEngineVersion(it) }

    fun reset() {
        withHandle { check(nativeReset(it)) { "native_reset_rejected" } }
    }

    fun processAsciiKey(character: Char, mask: Int = 0): Boolean {
        require(character.code in 0x20..0x7E) { "ascii_key_required" }
        return withHandle { nativeProcessKey(it, character.code, mask) }
    }

    fun snapshot(): Snapshot = withHandle { handle ->
        val flattened = nativeSnapshot(handle)
        check(flattened.size >= 2 && flattened.size % 2 == 0) {
            "native_snapshot_shape_invalid"
        }
        val candidates = ArrayList<Candidate>((flattened.size - 2) / 2)
        var index = 2
        while (index < flattened.size) {
            candidates += Candidate(
                text = flattened[index],
                comment = flattened[index + 1],
            )
            index += 2
        }
        Snapshot(
            composition = flattened[0],
            highlightedCandidateIndex = flattened[1].toInt(),
            candidates = candidates,
        )
    }

    fun selectCandidate(index: Int) {
        require(index >= 0) { "candidate_index_invalid" }
        withHandle { check(nativeSelectCandidate(it, index)) { "candidate_rejected" } }
    }

    fun takeCommit(): String? = withHandle { nativeTakeCommit(it) }

    override fun close() {
        checkThread()
        val handle = nativeHandle
        if (handle != 0L) {
            nativeHandle = 0L
            nativeClose(handle)
        }
    }

    private inline fun <T> withHandle(block: (Long) -> T): T {
        checkThread()
        check(nativeHandle != 0L) { "engine_closed" }
        return block(nativeHandle)
    }

    private fun checkThread() {
        check(Thread.currentThread().id == ownerThreadId) {
            "engine_thread_violation"
        }
    }

    companion object {
        init {
            System.loadLibrary("clipvault_rime_poc")
        }

        fun open(sharedDataDir: File, userDataDir: File): RimeNativeEngine {
            val shared = sharedDataDir.canonicalFile
            val user = userDataDir.canonicalFile
            require(shared != user) { "data_directories_must_differ" }
            val handle = nativeOpen(shared.path, user.path)
            check(handle != 0L) { "native_open_returned_zero" }
            return RimeNativeEngine(handle, Thread.currentThread().id)
        }

        @JvmStatic
        private external fun nativeOpen(
            sharedDataDir: String,
            userDataDir: String,
        ): Long

        @JvmStatic
        private external fun nativeClose(handle: Long)

        @JvmStatic
        private external fun nativeReset(handle: Long): Boolean

        @JvmStatic
        private external fun nativeProcessKey(
            handle: Long,
            keycode: Int,
            mask: Int,
        ): Boolean

        @JvmStatic
        private external fun nativeSnapshot(handle: Long): Array<String>

        @JvmStatic
        private external fun nativeSelectCandidate(
            handle: Long,
            index: Int,
        ): Boolean

        @JvmStatic
        private external fun nativeTakeCommit(handle: Long): String?

        @JvmStatic
        private external fun nativeEngineVersion(handle: Long): String
    }
}
