package com.clipvault.app.ime

import android.content.Context
import android.inputmethodservice.InputMethodService
import android.os.Build
import android.view.inputmethod.InputMethodManager

/**
 * Leaves ClipVault without calling the API 28-only previous-IME helper on the
 * Android 8.0/8.1 devices supported by this app.
 */
internal fun InputMethodService.switchToPreviousInputMethodCompat() {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
        if (switchToPreviousInputMethod()) return
    }

    // Android 8.0/8.1 has no public previous-IME service API. The picker is
    // also a useful fallback on newer devices when no previous IME is known.
    val inputMethodManager =
        getSystemService(Context.INPUT_METHOD_SERVICE) as? InputMethodManager
    inputMethodManager?.showInputMethodPicker()
}
