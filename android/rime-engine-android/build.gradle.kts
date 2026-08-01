import groovy.json.JsonSlurper
import java.security.MessageDigest

plugins {
    id("com.android.library")
    id("org.jetbrains.kotlin.android")
}

val productionTaskRequested = gradle.startParameter.taskNames.any { taskName ->
    val normalized = taskName.trimStart(':')
    taskName.contains("ProductionIme", ignoreCase = true) ||
        (
            taskName.contains("Release", ignoreCase = true) &&
                (
                    normalized.startsWith("ime-app:", ignoreCase = true) ||
                        normalized.startsWith("rime-engine-android:", ignoreCase = true)
                )
        )
}
val nativeEnabled = providers.gradleProperty("clipvaultRimeNativeEnabled")
    .map(String::toBoolean)
    .orElse(productionTaskRequested)
val librimeSource = providers.gradleProperty("clipvaultLibrimeSource")
val librimeBuild = providers.gradleProperty("clipvaultLibrimeBuild")
val librimeBuildArm64 = providers.gradleProperty("clipvaultLibrimeBuildArm64")
val librimeBuildX8664 = providers.gradleProperty("clipvaultLibrimeBuildX8664")
val nativePrebuiltRoot = providers.gradleProperty("clipvaultNativePrebuiltRoot")
val rimeDataDir = providers.gradleProperty("clipvaultRimeDataDir")
val sharedRimeAssetsDir = rootProject.projectDir.parentFile.resolve("shared-input/rime")
val sharedRimeAssetLock = sharedRimeAssetsDir.resolve("RIME_ASSET_LOCK.json")
require(sharedRimeAssetLock.isFile) { "Rime asset lock is missing: $sharedRimeAssetLock" }

@Suppress("UNCHECKED_CAST")
val rimeAssetLock = JsonSlurper().parse(sharedRimeAssetLock) as Map<String, Any?>
val allowedStagedRimeFiles = (rimeAssetLock["allowed_staged_files"] as? List<*>)
    ?.map { it as? String ?: error("Rime asset lock contains a non-string staged file") }
    ?.toSet()
    ?: error("Rime asset lock has no allowed_staged_files")
require(allowedStagedRimeFiles.isNotEmpty()) { "Rime asset allowlist must not be empty" }
require(allowedStagedRimeFiles.all { it == File(it).name && '/' !in it && '\\' !in it }) {
    "Rime staged asset names must be top-level basenames: $allowedStagedRimeFiles"
}

fun lockedAssetHashes(section: String): Map<String, String> {
    val sectionValue = rimeAssetLock[section] as? Map<*, *>
        ?: error("Rime asset lock has no $section section")
    val assets = if (section == "dictionary_source") {
        sectionValue["assets"] as? Map<*, *>
            ?: error("Rime dictionary_source has no assets section")
    } else {
        sectionValue
    }
    return assets.entries.associate { (name, hash) ->
        (name as? String ?: error("Rime asset lock has a non-string asset name")) to
            (hash as? String ?: error("Rime asset lock has a non-string SHA-256"))
                .lowercase()
    }
}

val canonicalRimeAssetHashes = lockedAssetHashes("canonical_assets")
val dictionaryRimeAssetHashes = lockedAssetHashes("dictionary_source")
val allowedRimeAssetHashes = canonicalRimeAssetHashes + dictionaryRimeAssetHashes
require(allowedRimeAssetHashes.keys == allowedStagedRimeFiles) {
    "Rime lock hash entries must exactly match allowed_staged_files. " +
        "Allowed=$allowedStagedRimeFiles hashed=${allowedRimeAssetHashes.keys}"
}

fun sha256(file: File): String = MessageDigest.getInstance("SHA-256")
    .digest(file.readBytes())
    .joinToString("") { "%02x".format(it) }

val generatedRimeAssetsDir = layout.buildDirectory.dir("generated/rimeAssets/rime")

android {
    namespace = "com.clipvault.ime.rime"
    compileSdk = 36
    ndkVersion = "28.0.13004108"

    defaultConfig {
        minSdk = 26
        if (nativeEnabled.get()) {
            ndk { abiFilters += setOf("arm64-v8a", "x86_64") }
            externalNativeBuild {
                cmake {
                    targets += "clipvault_rime_jni"
                    val buildArguments = if (librimeBuild.isPresent) {
                        listOf("-DCLIPVAULT_LIBRIME_BUILD=${librimeBuild.get()}")
                    } else {
                        listOf(
                            "-DCLIPVAULT_LIBRIME_BUILD_ARM64=${librimeBuildArm64.get()}",
                            "-DCLIPVAULT_LIBRIME_BUILD_X86_64=${librimeBuildX8664.get()}",
                        )
                    }
                    arguments += listOf(
                        "-DANDROID_STL=c++_shared",
                        "-DCLIPVAULT_LIBRIME_SOURCE=${librimeSource.get()}",
                        "-DCLIPVAULT_PREBUILT_ROOT=${nativePrebuiltRoot.get()}",
                    ) + buildArguments
                }
            }
        }
    }
    if (nativeEnabled.get()) {
        externalNativeBuild {
            cmake {
                path = file("src/main/cpp/CMakeLists.txt")
                version = "3.22.1"
            }
        }
    }
    kotlinOptions { jvmTarget = "17" }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

val generateRimeAssets by tasks.registering(Sync::class) {
    into(generatedRimeAssetsDir)
    from(sharedRimeAssetsDir) {
        include(allowedStagedRimeFiles)
    }
    from("RIME_PRODUCTION_LOCK.json") {
        into("third_party")
    }
    if (rimeDataDir.isPresent) {
        from(rimeDataDir) {
            include(allowedStagedRimeFiles)
        }
    }
    if (nativeEnabled.get()) {
        val sourceRoot = file(librimeSource.get())
        val dataRoot = file(rimeDataDir.get())
        from(sourceRoot.resolve("LICENSE")) {
            into("third_party")
            rename { "librime-BSD-3-Clause.txt" }
        }
        from(sourceRoot.resolve("deps/yaml-cpp/LICENSE")) {
            into("third_party")
            rename { "yaml-cpp-MIT.txt" }
        }
        from(sourceRoot.resolve("deps/leveldb/LICENSE")) {
            into("third_party")
            rename { "leveldb-BSD-3-Clause.txt" }
        }
        from(sourceRoot.resolve("deps/opencc/LICENSE")) {
            into("third_party")
            rename { "opencc-Apache-2.0.txt" }
        }
        from(sourceRoot.resolve("deps/marisa-trie/COPYING.md")) {
            into("third_party")
            rename { "marisa-BSD-2-Clause.txt" }
        }
        from(dataRoot.resolve("LICENSE")) {
            into("third_party")
            rename { "rime-pinyin-simp-Apache-2.0.txt" }
        }
    }
    doFirst {
        require(sharedRimeAssetsDir.isDirectory) {
            "Canonical Rime asset directory is missing: $sharedRimeAssetsDir"
        }
        val missing = canonicalRimeAssetHashes.keys.filterNot { name ->
            sharedRimeAssetsDir.resolve(name).isFile
        }
        require(missing.isEmpty()) { "Canonical Rime assets are missing: $missing" }
        val expectedFiles = if (rimeDataDir.isPresent) {
            allowedStagedRimeFiles
        } else {
            canonicalRimeAssetHashes.keys
        }
        expectedFiles.forEach { name ->
            val source = sharedRimeAssetsDir.resolve(name).takeIf(File::isFile)
                ?: rimeDataDir.orNull?.let(::file)?.resolve(name)?.takeIf(File::isFile)
                ?: error("Locked Rime asset is missing from its source: $name")
            val actualHash = sha256(source)
            require(actualHash == allowedRimeAssetHashes.getValue(name)) {
                "Locked Rime asset SHA-256 mismatch for $source. " +
                    "Expected ${allowedRimeAssetHashes.getValue(name)}, got $actualHash"
            }
        }
        val legacyAssets = file("src/main/rime")
            .takeIf(File::exists)
            ?.walkTopDown()
            ?.filter(File::isFile)
            ?.toList()
            .orEmpty()
        require(legacyAssets.isEmpty()) {
            "Module-local Rime assets are forbidden; use shared-input/rime: $legacyAssets"
        }
    }
    doLast {
        val expectedFiles = if (rimeDataDir.isPresent) {
            allowedStagedRimeFiles
        } else {
            canonicalRimeAssetHashes.keys
        }
        val outputRoot = generatedRimeAssetsDir.get().asFile
        val actualFiles = outputRoot.listFiles()
            ?.filter(File::isFile)
            ?.map(File::getName)
            ?.toSet()
            .orEmpty()
        require(actualFiles == expectedFiles) {
            "Generated Rime assets do not match the lock allowlist. " +
                "Expected=$expectedFiles actual=$actualFiles"
        }
        actualFiles.forEach { name ->
            val output = outputRoot.resolve(name)
            val actualHash = sha256(output)
            require(actualHash == allowedRimeAssetHashes.getValue(name)) {
                "Generated Rime asset SHA-256 mismatch for $name. " +
                    "Expected ${allowedRimeAssetHashes.getValue(name)}, got $actualHash"
            }
        }
    }
}

android.sourceSets.getByName("main").assets.srcDir(
    layout.buildDirectory.dir("generated/rimeAssets"),
)
tasks.named("preBuild").configure { dependsOn(generateRimeAssets) }

dependencies {
    implementation(project(":ime-engine"))
    testImplementation("junit:junit:4.13.2")
}
