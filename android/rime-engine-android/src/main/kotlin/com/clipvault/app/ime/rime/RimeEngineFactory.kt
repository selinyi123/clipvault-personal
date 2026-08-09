package com.clipvault.app.ime.rime

import android.content.Context
import com.clipvault.ime.engine.DirectInputEngineAdapter
import com.clipvault.ime.engine.EngineFieldKind
import com.clipvault.ime.engine.EngineInputContext
import com.clipvault.ime.engine.InputEngineAdapterV2
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

private const val RIME_WARMUP_RETRY_BACKOFF_MS = 5_000L

enum class RimeReadiness { IDLE, WARMING, READY, FAILED }

object RimeEngineFactory {
    private val warmupScheduled = AtomicBoolean(false)
    private val executor = Executors.newSingleThreadExecutor { task ->
        Thread(task, "clipvault-rime-warmup").apply { isDaemon = true }
    }
    @Volatile private var readiness = RimeReadiness.IDLE
    @Volatile private var warmupDurationMs = -1L
    @Volatile private var retryAfterElapsedMs = 0L

    /** Starts deployment/maintenance off the IME main and key paths. */
    fun prewarmAsync(context: Context) {
        val now = android.os.SystemClock.elapsedRealtime()
        if (readiness == RimeReadiness.FAILED && now < retryAfterElapsedMs) return
        if (!warmupScheduled.compareAndSet(false, true)) return
        readiness = RimeReadiness.WARMING
        val applicationContext = context.applicationContext
        executor.execute {
            val startedAt = android.os.SystemClock.elapsedRealtime()
            val succeeded = try {
                val paths = RimeDataInstaller.prepare(applicationContext)
                NativeRimeBridge.ensureLoaded()
                NativeRimeBridge.initialize(paths.sharedDir.path, paths.userDir.path)
            } catch (_: Exception) {
                false
            } catch (_: UnsatisfiedLinkError) {
                false
            }
            warmupDurationMs = android.os.SystemClock.elapsedRealtime() - startedAt
            if (succeeded) {
                retryAfterElapsedMs = 0L
                readiness = RimeReadiness.READY
            } else {
                val retryBase = android.os.SystemClock.elapsedRealtime()
                retryAfterElapsedMs = if (
                    retryBase > Long.MAX_VALUE - RIME_WARMUP_RETRY_BACKOFF_MS
                ) {
                    Long.MAX_VALUE
                } else {
                    retryBase + RIME_WARMUP_RETRY_BACKOFF_MS
                }
                readiness = RimeReadiness.FAILED
                // A transient deploy/native-loader failure must not pin this
                // process to Direct forever. Future input sessions may retry
                // after a short backoff, while repeated failures stay bounded.
                warmupScheduled.set(false)
            }
        }
    }

    fun isReady(): Boolean = readiness == RimeReadiness.READY

    fun readiness(): RimeReadiness = readiness

    fun lastWarmupDurationMs(): Long = warmupDurationMs

    /**
     * Normal and incognito text fields use native Rime; incognito sessions
     * select the schema with user-dictionary learning disabled. Passwords use
     * the intentional Direct path. Callers can gate WARMING separately so an
     * unfinished first deployment never turns intended Chinese into Latin.
     */
    fun create(context: Context, input: EngineInputContext): InputEngineAdapterV2 {
        if (input.fieldKind == EngineFieldKind.PASSWORD) {
            return DirectInputEngineAdapter()
        }
        // Never deploy or join Rime maintenance from onStartInput. The IME
        // shell gates a WARMING engine and upgrades the live editor only after
        // readiness, so an intended Chinese sequence is never silently Latin.
        if (!isReady()) return DirectInputEngineAdapter()
        return NativeRimeInputEngineAdapter(NativeRimeBridge)
    }
}
