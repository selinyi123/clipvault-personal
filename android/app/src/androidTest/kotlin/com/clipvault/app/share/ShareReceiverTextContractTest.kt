package com.clipvault.app.share

import android.content.Intent
import android.text.SpannableString
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ShareReceiverTextContractTest {
    @Test
    fun styledCharSequenceExtraIsAcceptedAsPlainCaptureText() {
        val intent = Intent(Intent.ACTION_SEND).putExtra(
            Intent.EXTRA_TEXT,
            SpannableString("styled shared text"),
        )

        assertEquals("styled shared text", ShareReceiverActivity.extractSharedText(intent))
    }
}
