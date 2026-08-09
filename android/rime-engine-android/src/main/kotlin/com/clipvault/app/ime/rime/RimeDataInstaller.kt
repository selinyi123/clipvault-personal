package com.clipvault.app.ime.rime

import android.content.Context
import java.io.File
import java.io.IOException

internal data class RimeDataPaths(val sharedDir: File, val userDir: File)

internal object RimeDataInstaller {
    private const val ASSET_ROOT = "rime"
    private const val DATA_VERSION =
        "pinyin-simp-0c6861ef7420ee780270ca6d993d18d4101049d0-clipvault-schema-v3-punctuation"

    @Synchronized
    fun prepare(context: Context): RimeDataPaths {
        val sharedDir = File(context.noBackupFilesDir, "rime-shared/$DATA_VERSION")
        val marker = File(sharedDir, ".installed")
        val installed = marker.isFile && runCatching {
            marker.readText(Charsets.UTF_8) == DATA_VERSION
        }.getOrDefault(false)
        if (!installed) {
            if (!sharedDir.isDirectory && !sharedDir.mkdirs() && !sharedDir.isDirectory) {
                throw IOException("unable to create Rime shared data directory")
            }
            // A partial asset copy is safe to repair in this versioned,
            // app-owned directory.  Remove only the sentinel so every asset
            // is copied again before readiness can be published.
            marker.delete()
            copyAssetTree(context, ASSET_ROOT, sharedDir)
            val temporaryMarker = File(sharedDir, ".installed.tmp")
            temporaryMarker.writeText(DATA_VERSION, Charsets.UTF_8)
            if (!temporaryMarker.renameTo(marker)) {
                temporaryMarker.delete()
                throw IOException("unable to publish Rime data marker")
            }
        }
        val userDir = File(context.filesDir, "rime-user").apply { mkdirs() }
        return RimeDataPaths(sharedDir, userDir)
    }

    private fun copyAssetTree(context: Context, assetPath: String, output: File) {
        val children = context.assets.list(assetPath).orEmpty()
        if (children.isEmpty()) {
            output.parentFile?.mkdirs()
            context.assets.open(assetPath).use { input ->
                output.outputStream().use(input::copyTo)
            }
            return
        }
        output.mkdirs()
        children.forEach { child ->
            copyAssetTree(context, "$assetPath/$child", File(output, child))
        }
    }
}
