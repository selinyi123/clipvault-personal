package com.clipvault.imeapp

import java.io.File
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ManifestPermissionTest {
    @Test
    fun `isolated IME has signature bridge but no network or SMS permissions`() {
        val manifest = File(System.getProperty("user.dir"), "src/main/AndroidManifest.xml").readText()
        assertTrue(manifest.contains("com.clipvault.permission.RUNTIME_SNAPSHOT"))
        assertFalse(manifest.contains("android.permission.INTERNET"))
        assertFalse(manifest.contains("android.permission.ACCESS_NETWORK_STATE"))
        assertFalse(manifest.contains("android.permission.READ_SMS"))
        assertFalse(manifest.contains("android.permission.RECEIVE_SMS"))
        assertFalse(manifest.contains("android.permission.BIND_NOTIFICATION_LISTENER_SERVICE"))
        assertTrue(manifest.contains("ClipVaultImeSetupActivity"))
        assertTrue(manifest.contains("android.intent.category.LAUNCHER"))
    }
}
