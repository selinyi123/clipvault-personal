package com.clipvault.app.ime.rime

internal interface RimeNativeApi {
    fun initialize(sharedDir: String, userDir: String): Boolean
    fun createSession(): Long
    fun selectSchema(session: Long, schemaId: String): Boolean
    fun setOption(session: Long, option: String, enabled: Boolean)
    fun processKey(session: Long, keycode: Int, mask: Int): Boolean
    fun snapshot(session: Long): Array<String>
    fun takeCommit(session: Long): String?
    fun selectCandidate(session: Long, indexOnPage: Int): Boolean
    fun commitComposition(session: Long): Boolean
    fun clearComposition(session: Long)
    fun destroySession(session: Long)
}

internal object NativeRimeBridge : RimeNativeApi {
    @Volatile private var loaded = false

    @Synchronized
    fun ensureLoaded() {
        if (!loaded) {
            System.loadLibrary("clipvault_rime_jni")
            loaded = true
        }
    }

    override fun initialize(sharedDir: String, userDir: String) =
        nativeInitialize(sharedDir, userDir)
    override fun createSession() = nativeCreateSession()
    override fun selectSchema(session: Long, schemaId: String) =
        nativeSelectSchema(session, schemaId)
    override fun setOption(session: Long, option: String, enabled: Boolean) =
        nativeSetOption(session, option, enabled)
    override fun processKey(session: Long, keycode: Int, mask: Int) =
        nativeProcessKey(session, keycode, mask)
    override fun snapshot(session: Long) = nativeSnapshot(session)
    override fun takeCommit(session: Long) = nativeTakeCommit(session)
    override fun selectCandidate(session: Long, indexOnPage: Int) =
        nativeSelectCandidate(session, indexOnPage)
    override fun commitComposition(session: Long) = nativeCommitComposition(session)
    override fun clearComposition(session: Long) = nativeClearComposition(session)
    override fun destroySession(session: Long) = nativeDestroySession(session)

    private external fun nativeInitialize(sharedDir: String, userDir: String): Boolean
    private external fun nativeCreateSession(): Long
    private external fun nativeSelectSchema(session: Long, schemaId: String): Boolean
    private external fun nativeSetOption(session: Long, option: String, enabled: Boolean)
    private external fun nativeProcessKey(session: Long, keycode: Int, mask: Int): Boolean
    private external fun nativeSnapshot(session: Long): Array<String>
    private external fun nativeTakeCommit(session: Long): String?
    private external fun nativeSelectCandidate(session: Long, indexOnPage: Int): Boolean
    private external fun nativeCommitComposition(session: Long): Boolean
    private external fun nativeClearComposition(session: Long)
    private external fun nativeDestroySession(session: Long)
}
