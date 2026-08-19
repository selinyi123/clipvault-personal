plugins {
    id("com.android.library")
    id("org.jetbrains.kotlin.android")
}

val productionTaskRequested = gradle.startParameter.taskNames.any { taskName ->
    taskName.contains("Release", ignoreCase = true) ||
        taskName.contains("ProductionIme", ignoreCase = true)
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
val requiredCanonicalRimeAssets = setOf(
    "default.yaml",
    "clipvault_pinyin.schema.yaml",
    "clipvault_pinyin_private.schema.yaml",
    "clipvault_punctuation.yaml",
)

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
    into(layout.buildDirectory.dir("generated/rimeAssets/rime"))
    from(sharedRimeAssetsDir)
    from("RIME_PRODUCTION_LOCK.json") {
        into("third_party")
    }
    if (rimeDataDir.isPresent) {
        from(rimeDataDir) {
            include("*.yaml", "LICENSE", "AUTHORS")
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
        val missing = requiredCanonicalRimeAssets.filterNot { name ->
            sharedRimeAssetsDir.resolve(name).isFile
        }
        require(missing.isEmpty()) { "Canonical Rime assets are missing: $missing" }
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
}

android.sourceSets.getByName("main").assets.srcDir(
    layout.buildDirectory.dir("generated/rimeAssets"),
)
tasks.named("preBuild").configure { dependsOn(generateRimeAssets) }

dependencies {
    implementation(project(":ime-engine"))
    testImplementation("junit:junit:4.13.2")
}
