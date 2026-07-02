package com.clipvault.app.ime

/**
 * Main-thread input-session token for personal ClipVault actions.
 *
 * Candidate loads run off-thread, so checking the field only before launching
 * a query is insufficient: its result may arrive after Android switches to a
 * password/incognito editor. A token is valid only for the input generation
 * that created it and only while that generation permits personal data.
 */
internal data class ImePrivacyToken(
    val generation: Long,
    val suppressPersonalData: Boolean,
)

internal class ImePrivacySession {
    @Volatile
    private var current = ImePrivacyToken(generation = 0, suppressPersonalData = true)

    /** Called from InputMethodService.onStartInput (the main thread). */
    fun begin(suppressPersonalData: Boolean): ImePrivacyToken {
        val next = ImePrivacyToken(current.generation + 1, suppressPersonalData)
        current = next
        return next
    }

    /** Invalidates every in-flight result and fails closed between editors. */
    fun end() {
        begin(suppressPersonalData = true)
    }

    fun token(): ImePrivacyToken = current

    fun isCurrent(token: ImePrivacyToken): Boolean = token == current

    fun allowsPersonalData(token: ImePrivacyToken = current): Boolean =
        token == current && !token.suppressPersonalData
}
