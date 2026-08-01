package com.clipvault.app.otp

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class OtpEndpointPolicyTest {
    @Test
    fun cleartextAllowsOnlyLoopbackOrLiteralTailscaleAddresses() {
        assertTrue(isOtpTransportBaseUrlAllowed("http://127.0.0.1:8787/api"))
        assertTrue(isOtpTransportBaseUrlAllowed("http://100.64.0.1:8787/api"))
        assertTrue(isOtpTransportBaseUrlAllowed("http://100.127.255.254:8787/api"))
        assertTrue(isOtpTransportBaseUrlAllowed("http://[::1]:8787/api"))
        assertTrue(isOtpTransportBaseUrlAllowed("http://[fd7a:115c:a1e0::1]:8787/api"))

        assertFalse(isOtpTransportBaseUrlAllowed("http://192.168.1.20:8787/api"))
        assertFalse(isOtpTransportBaseUrlAllowed("http://10.0.0.2:8787/api"))
        assertFalse(isOtpTransportBaseUrlAllowed("http://100.128.0.1:8787/api"))
        assertFalse(isOtpTransportBaseUrlAllowed("http://desktop.tailnet.ts.net:8787/api"))
        assertFalse(isOtpTransportBaseUrlAllowed("http://[fd7a:115c:a1e1::1]:8787/api"))
    }

    @Test
    fun tlsHostIsAllowedButCredentialsAndNonHttpSchemesAreRejected() {
        assertTrue(isOtpTransportBaseUrlAllowed("https://desktop.example/api"))
        assertFalse(isOtpTransportBaseUrlAllowed("http://token@100.64.0.1/api"))
        assertFalse(isOtpTransportBaseUrlAllowed("ftp://100.64.0.1/api"))
        assertFalse(isOtpTransportBaseUrlAllowed("not-a-url"))
    }
}
