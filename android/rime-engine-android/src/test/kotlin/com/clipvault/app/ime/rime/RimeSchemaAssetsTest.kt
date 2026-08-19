package com.clipvault.app.ime.rime

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

class RimeSchemaAssetsTest {
    @Test
    fun `private schema remains Chinese capable without a user dictionary`() {
        val schema = canonicalAsset("clipvault_pinyin_private.schema.yaml").readText()

        assertTrue(schema.contains("dictionary: pinyin_simp"))
        assertTrue(schema.contains("enable_sentence: true"))
        assertTrue(schema.contains("enable_user_dict: false"))
    }

    @Test
    fun `normal and private schemas share the self contained punctuation preset`() {
        listOf(
            "clipvault_pinyin.schema.yaml",
            "clipvault_pinyin_private.schema.yaml",
        ).forEach { fileName ->
            val schema = canonicalAsset(fileName).readText()
            assertTrue("$fileName must process punctuation", schema.contains("- punctuator"))
            assertTrue("$fileName must segment punctuation", schema.contains("- punct_segmentor"))
            assertTrue("$fileName must translate punctuation", schema.contains("- punct_translator"))
            assertTrue(
                "$fileName must use the ClipVault-owned preset",
                schema.contains("import_preset: clipvault_punctuation"),
            )
        }

        val punctuation = canonicalAsset("clipvault_punctuation.yaml").readText()
        assertTrue(punctuation.contains("',' : { commit: ， }"))
        assertTrue(punctuation.contains("'.' : { commit: 。 }"))
        assertTrue(punctuation.contains("'?' : { commit: ？ }"))
        assertTrue(punctuation.contains("'!' : { commit: ！ }"))
        assertTrue(punctuation.contains("{ pair: [ '“', '”' ] }"))
        assertTrue(!punctuation.contains("import_preset:"))
    }

    private fun canonicalAsset(name: String): File {
        var directory = File(requireNotNull(System.getProperty("user.dir"))).canonicalFile
        repeat(6) {
            val candidate = directory.resolve("shared-input/rime/$name")
            if (candidate.isFile) return candidate
            directory = directory.parentFile ?: error("Could not locate shared-input/rime/$name")
        }
        error("Could not locate shared-input/rime/$name")
    }
}
