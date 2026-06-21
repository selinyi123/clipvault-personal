package com.clipvault.app.ime

import android.text.InputType
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PrivacyAwareFilterTest {
    @Test
    fun suppressesNoSuggestionsTextFields() {
        val inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS

        assertTrue(PrivacyAwareFilter.shouldSuppressCandidates(inputType))
    }

    @Test
    fun suppressesTextPasswords() {
        val inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD

        assertTrue(PrivacyAwareFilter.shouldSuppressCandidates(inputType))
    }

    @Test
    fun suppressesVisibleTextPasswords() {
        val inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD

        assertTrue(PrivacyAwareFilter.shouldSuppressCandidates(inputType))
    }

    @Test
    fun suppressesWebPasswords() {
        val inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_WEB_PASSWORD

        assertTrue(PrivacyAwareFilter.shouldSuppressCandidates(inputType))
    }

    @Test
    fun suppressesNumericPasswords() {
        val inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_VARIATION_PASSWORD

        assertTrue(PrivacyAwareFilter.shouldSuppressCandidates(inputType))
    }

    @Test
    fun allowsOrdinaryText() {
        val inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_NORMAL

        assertFalse(PrivacyAwareFilter.shouldSuppressCandidates(inputType))
    }

    @Test
    fun allowsOrdinaryNumbers() {
        val inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_VARIATION_NORMAL

        assertFalse(PrivacyAwareFilter.shouldSuppressCandidates(inputType))
    }
}
