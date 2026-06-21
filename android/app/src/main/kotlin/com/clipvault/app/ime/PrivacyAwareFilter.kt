package com.clipvault.app.ime

import android.text.InputType
import android.view.inputmethod.EditorInfo

/**
 * Runtime guard before showing or committing ClipVault candidates from an IME.
 *
 * This is intentionally conservative: ClipVault suggestions can contain long
 * snippets, commands, prompts, paths, or synced desktop text. In password-like
 * or app-declared no-suggestions fields, showing those candidates is the wrong
 * default even when the user explicitly opened the keyboard panel.
 */
object PrivacyAwareFilter {
    fun shouldSuppressCandidates(info: EditorInfo?): Boolean =
        info?.let { shouldSuppressCandidates(it.inputType) } ?: false

    internal fun shouldSuppressCandidates(inputType: Int): Boolean {
        val klass = inputType and InputType.TYPE_MASK_CLASS
        val variation = inputType and InputType.TYPE_MASK_VARIATION
        val flags = inputType and InputType.TYPE_MASK_FLAGS

        if ((flags and InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS) != 0) return true

        return when (klass) {
            InputType.TYPE_CLASS_TEXT -> variation == InputType.TYPE_TEXT_VARIATION_PASSWORD ||
                variation == InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD ||
                variation == InputType.TYPE_TEXT_VARIATION_WEB_PASSWORD
            InputType.TYPE_CLASS_NUMBER -> variation == InputType.TYPE_NUMBER_VARIATION_PASSWORD
            else -> false
        }
    }

    fun suppressionMessage(): String = "当前输入框为密码/敏感或禁建议字段，ClipVault 候选已隐藏。"
}
