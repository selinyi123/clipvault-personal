import java.util.zip.ZipFile

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.clipvault.imeapp"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.clipvault.ime"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "2.2.0-dev"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }
    buildTypes {
        release { isMinifyEnabled = false }
    }
    kotlinOptions { jvmTarget = "17" }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    implementation(project(":ime-engine"))
    implementation(project(":rime-engine-android"))
    testImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test:runner:1.6.2")
}

val verifyProductionImeApk by tasks.registering {
    group = "verification"
    description = "Builds the release IME and verifies native dual-ABI/runtime assets."
    dependsOn("assembleRelease")
    doLast {
        val apks = layout.buildDirectory.dir("outputs/apk/release").get().asFile
            .listFiles { file -> file.extension == "apk" }
            .orEmpty()
        require(apks.size == 1) { "Expected one release IME APK, found ${apks.toList()}" }
        val requiredEntries = setOf(
            "lib/arm64-v8a/libclipvault_rime_jni.so",
            "lib/arm64-v8a/libc++_shared.so",
            "lib/x86_64/libclipvault_rime_jni.so",
            "lib/x86_64/libc++_shared.so",
            "assets/rime/default.yaml",
            "assets/rime/clipvault_pinyin.schema.yaml",
            "assets/rime/clipvault_pinyin_private.schema.yaml",
            "assets/rime/clipvault_punctuation.yaml",
            "assets/rime/pinyin_simp.dict.yaml",
            "assets/third_party/NOTICE.txt",
            "assets/rime/third_party/RIME_PRODUCTION_LOCK.json",
            "assets/rime/third_party/librime-BSD-3-Clause.txt",
            "assets/rime/third_party/yaml-cpp-MIT.txt",
            "assets/rime/third_party/leveldb-BSD-3-Clause.txt",
            "assets/rime/third_party/opencc-Apache-2.0.txt",
            "assets/rime/third_party/marisa-BSD-2-Clause.txt",
            "assets/rime/third_party/rime-pinyin-simp-Apache-2.0.txt",
        )
        ZipFile(apks.single()).use { apk ->
            val entries = apk.entries().asSequence().map { it.name }.toSet()
            val missing = requiredEntries - entries
            require(missing.isEmpty()) { "Production IME APK is missing $missing" }
        }
        println("Verified production IME APK: ${apks.single()}")
    }
}

tasks.register("buildProductionIme") {
    group = "build"
    description = "Fail-closed production entrypoint for the native standalone IME."
    dependsOn(verifyProductionImeApk)
}
