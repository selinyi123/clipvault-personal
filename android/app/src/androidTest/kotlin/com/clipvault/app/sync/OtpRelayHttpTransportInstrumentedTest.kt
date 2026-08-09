package com.clipvault.app.sync

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class OtpRelayHttpTransportInstrumentedTest {
    @Test
    fun missingStoredBearerIsReportedAsUnpairedNotTransientFailure() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        val settings = Settings(context)
        settings.host = "127.0.0.1"
        settings.token = null

        assertEquals(
            OtpPairedEndpointAcquireResult.AuthRequired,
            SettingsOtpPairedEndpointPort(settings).acquire(),
        )
    }
}
