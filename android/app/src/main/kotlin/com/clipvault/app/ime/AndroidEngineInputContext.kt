package com.clipvault.app.ime

import android.text.InputType
import android.view.inputmethod.EditorInfo
import com.clipvault.ime.engine.EngineFieldKind
import com.clipvault.ime.engine.EngineInputContext

internal object AndroidEngineInputContext {
    fun from(info: EditorInfo?): EngineInputContext {
        val inputType = info?.inputType ?: InputType.TYPE_NULL
        val imeOptions = info?.imeOptions ?: EditorInfo.IME_FLAG_NO_PERSONALIZED_LEARNING
        val fieldKind = fieldKind(inputType)
        val incognito = imeOptions and EditorInfo.IME_FLAG_NO_PERSONALIZED_LEARNING != 0
        val suppressPersonalData = PrivacyAwareFilter.shouldSuppressCandidates(info)
        return EngineInputContext(
            fieldKind = fieldKind,
            incognito = incognito,
            learningAllowed = !suppressPersonalData,
            clipVaultAllowed = !suppressPersonalData,
        )
    }

    private fun fieldKind(inputType: Int): EngineFieldKind {
        val klass = inputType and InputType.TYPE_MASK_CLASS
        val variation = inputType and InputType.TYPE_MASK_VARIATION
        return when (klass) {
            InputType.TYPE_CLASS_TEXT -> when (variation) {
                InputType.TYPE_TEXT_VARIATION_PASSWORD,
                InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD,
                InputType.TYPE_TEXT_VARIATION_WEB_PASSWORD -> EngineFieldKind.PASSWORD
                InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS,
                InputType.TYPE_TEXT_VARIATION_WEB_EMAIL_ADDRESS -> EngineFieldKind.EMAIL
                InputType.TYPE_TEXT_VARIATION_URI -> EngineFieldKind.URL
                else -> EngineFieldKind.TEXT
            }
            InputType.TYPE_CLASS_NUMBER ->
                if (variation == InputType.TYPE_NUMBER_VARIATION_PASSWORD) {
                    EngineFieldKind.PASSWORD
                } else {
                    EngineFieldKind.NUMBER
                }
            InputType.TYPE_CLASS_PHONE -> EngineFieldKind.PHONE
            else -> EngineFieldKind.UNKNOWN
        }
    }
}
