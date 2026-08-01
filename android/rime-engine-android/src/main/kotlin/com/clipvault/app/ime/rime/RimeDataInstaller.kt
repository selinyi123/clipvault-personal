package com.clipvault.app.ime.rime

import android.content.Context
import java.io.File

internal data class RimeDataPaths(val sharedDir: File, val userDir: File)

internal object RimeDataInstaller {
    private const val ASSET_ROOT = "rime"
    private const val DATA_VERSION =
        "pinyin-simp-0c6861ef7420ee780270ca6d993d18d4101049d0-clipvault-schema-v3-punctuation"

    fun prepare(context: Context): RimeDataPaths {
        val sharedDir = File(context.noBackupFilesDir, "rime-shared/$DATA_VERSION")
        val marker = File(sharedDir, ".installed")
        if (!marker.isFile) {
            sharedDir.mkdirs()
            copyAssetTree(context, ASSET_ROOT, sharedDir)
            marker.writeText(DATA_VERSION, Charsets.UTF_8)
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
