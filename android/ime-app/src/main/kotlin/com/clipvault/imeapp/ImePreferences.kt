package com.clipvault.imeapp

import android.content.Context

internal object ImePreferences {
    const val FILE_NAME = "clipvault_ime_settings"
    const val KEY_SENSITIVE_PACKAGES = "sensitive_packages"
    const val KEY_HAPTIC_FEEDBACK = "haptic_feedback"

    val defaultSensitivePackages: Set<String> = setOf(
        "com.android.settings",
        "com.azure.authenticator",
        "com.bitwarden.app",
        "com.google.android.apps.authenticator2",
        "com.lastpass.lpandroid",
        "com.onepassword.android",
        "com.twofasapp",
        "keepass2android.keepass2android",
    )

    fun readSensitivePackages(context: Context): Set<String>? = try {
        val preferences = context.getSharedPreferences(FILE_NAME, Context.MODE_PRIVATE)
        if (!preferences.contains(KEY_SENSITIVE_PACKAGES)) {
            defaultSensitivePackages
        } else {
            preferences.getStringSet(KEY_SENSITIVE_PACKAGES, null)
                ?.mapNotNull(::normalizePackageName)
                ?.toSet()
        }
    } catch (_: RuntimeException) {
        null
    }

    fun writeSensitivePackages(context: Context, packages: Set<String>): Boolean = try {
        val normalized = packages.mapNotNull(::normalizePackageName).toSet()
        context.getSharedPreferences(FILE_NAME, Context.MODE_PRIVATE)
            .edit()
            .putStringSet(KEY_SENSITIVE_PACKAGES, normalized)
            .commit()
    } catch (_: RuntimeException) {
        false
    }

    fun hapticFeedbackEnabled(context: Context): Boolean = try {
        context.getSharedPreferences(FILE_NAME, Context.MODE_PRIVATE)
            .getBoolean(KEY_HAPTIC_FEEDBACK, true)
    } catch (_: RuntimeException) {
        false
    }

    fun setHapticFeedbackEnabled(context: Context, enabled: Boolean): Boolean = try {
        context.getSharedPreferences(FILE_NAME, Context.MODE_PRIVATE)
            .edit()
            .putBoolean(KEY_HAPTIC_FEEDBACK, enabled)
            .commit()
    } catch (_: RuntimeException) {
        false
    }

    fun parsePackageList(value: String): Set<String> = value
        .split(',', ';', '\n', '\r', '\t', ' ')
        .mapNotNull(::normalizePackageName)
        .toSet()

    private fun normalizePackageName(value: String): String? = value
        .trim()
        .lowercase()
        .takeIf { PACKAGE_NAME.matches(it) }

    private val PACKAGE_NAME = Regex("[a-z0-9_]+(?:\\.[a-z0-9_]+)+")
}
