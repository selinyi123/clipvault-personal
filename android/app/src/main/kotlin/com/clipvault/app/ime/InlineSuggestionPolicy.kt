package com.clipvault.app.ime

import android.os.Build

/** Platform gate for the system-owned inline Autofill surface. */
internal object InlineSuggestionPolicy {
    fun isSupported(sdkInt: Int = Build.VERSION.SDK_INT): Boolean =
        sdkInt >= Build.VERSION_CODES.R
}
