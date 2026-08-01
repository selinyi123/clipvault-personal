package com.clipvault.app.privacy

import java.nio.file.Files
import java.nio.file.Path
import javax.xml.parsers.DocumentBuilderFactory
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RuntimeImeRemovalSourceTest {
    private val project = Path.of(System.getProperty("user.dir"))

    @Test
    fun networkedRuntimeContainsNoImeDeclarationSourceOrRimeDependency() {
        val manifest = project.resolve("src/main/AndroidManifest.xml")
        val document = DocumentBuilderFactory.newInstance().apply {
            isNamespaceAware = true
        }.newDocumentBuilder().parse(manifest.toFile())
        val services = document.getElementsByTagName("service")
        for (index in 0 until services.length) {
            val service = services.item(index)
            val permission = service.attributes?.getNamedItemNS(
                "http://schemas.android.com/apk/res/android",
                "permission",
            )?.nodeValue
            assertFalse(
                "Runtime must not declare even a disabled InputMethodService",
                permission == "android.permission.BIND_INPUT_METHOD",
            )
        }

        val legacySource = project.resolve("src/main/kotlin/com/clipvault/app/ime")
        assertTrue(
            "legacy Runtime IME production source set must be absent or empty",
            !Files.exists(legacySource) || Files.list(legacySource).use { stream -> stream.findAny().isEmpty },
        )
        assertFalse(Files.exists(project.resolve("src/main/res/xml/ime_panel_config.xml")))
        assertFalse(Files.exists(project.resolve("src/main/res/xml/ime_full_config.xml")))

        val gradle = String(
            Files.readAllBytes(project.resolve("build.gradle.kts")),
            Charsets.UTF_8,
        )
        assertFalse(
            "Runtime must not package native Rime; only the standalone IME owns it",
            gradle.contains("implementation(project(\":rime-engine-android\"))"),
        )
    }
}
