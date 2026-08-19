package com.clipvault.imeapp

import android.app.Application
import com.clipvault.app.ime.rime.RimeEngineFactory

/** Owns background-only native data deployment for the no-network IME APK. */
class ClipVaultImeApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        RimeEngineFactory.prewarmAsync(this)
    }
}
