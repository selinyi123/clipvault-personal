import groovy.json.JsonSlurper
import java.security.MessageDigest

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.google.devtools.ksp")
}

val v2CandidateVersion = JsonSlurper().parse(
    rootProject.file("../contracts/v2_candidate_version.json")
) as Map<*, *>

val smsUserConsentLockFile = project.file("PLAY_SERVICES_SMS_USER_CONSENT_LOCK.json")
val smsUserConsentLock = JsonSlurper().parse(smsUserConsentLockFile) as Map<*, *>
val smsUserConsentVersion = smsUserConsentLock["version"] as String
val smsUserConsentCoordinate =
    "com.google.android.gms:play-services-auth-api-phone:$smsUserConsentVersion"
val smsUserConsentArtifact = smsUserConsentLock["artifact"] as Map<*, *>
val smsUserConsentPom = smsUserConsentLock["pom"] as Map<*, *>
val smsUserConsentAarSha256 = smsUserConsentArtifact["sha256"] as String
val smsUserConsentAarSize = (smsUserConsentArtifact["size_bytes"] as Number).toLong()
val smsUserConsentPomSha256 = smsUserConsentPom["sha256"] as String
val smsUserConsentPomSize = (smsUserConsentPom["size_bytes"] as Number).toLong()

check(smsUserConsentVersion == "18.2.0") {
    "SMS User Consent dependency version must remain frozen at 18.2.0"
}
check(smsUserConsentAarSha256 == "15963fa1cf08ad2778fd54f17ef72cb7597af15f40415885833b1240369230f3") {
    "SMS User Consent AAR lock hash drifted"
}
check(smsUserConsentPomSha256 == "1014bbbd9f385e57e1fb3f99d536e60e494b2ef7f4e3959088f748413a89a4b0") {
    "SMS User Consent POM lock hash drifted"
}

android {
    namespace = "com.clipvault.app"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.clipvault.app"
        minSdk = 26          // Android 8.0: Quick Settings Tile + modern clipboard rules
        targetSdk = 36
        versionCode = (v2CandidateVersion["android_runtime_version_code"] as Number).toInt()
        versionName = v2CandidateVersion["version_name"] as String
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        buildConfigField("boolean", "OTP_SMS_CAPTURE_INCLUDED", "false")
    }
    // Release signing reads from -P properties (or ~/.gradle), so the keystore
    // and passwords never live in the repo. Falls back gracefully when unset.
    signingConfigs {
        create("release") {
            val ksPath = (project.findProperty("CV_KEYSTORE") as String?)
            if (!ksPath.isNullOrBlank()) {
                storeFile = file(ksPath)
                storePassword = project.findProperty("CV_KEYSTORE_PASS") as String?
                keyAlias = (project.findProperty("CV_KEY_ALIAS") as String?) ?: "clipvault"
                keyPassword = (project.findProperty("CV_KEY_PASS") as String?)
            }
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = false   // no R8 for a self-use app: avoids Room/Compose keep-rule risk
            if (!(project.findProperty("CV_KEYSTORE") as String?).isNullOrBlank()) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
        create("otpSmsRelay") {
            initWith(getByName("release"))
            matchingFallbacks += listOf("release")
            buildConfigField("boolean", "OTP_SMS_CAPTURE_INCLUDED", "true")
        }
    }

    buildFeatures {
        compose = true   // compiler managed by kotlin.plugin.compose
        buildConfig = true
    }
    kotlinOptions { jvmTarget = "17" }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

val otpSmsApprovalGate = tasks.register("otpSmsRelayApprovalGate") {
    group = "verification"
    description = "Owner/Play/signing gate for the restricted RECEIVE_SMS release lane"
    doLast {
        val approval = providers.gradleProperty("CLIPVAULT_PLAY_SMS_APPROVAL_REF").orNull
        require(!approval.isNullOrBlank()) {
            "CLIPVAULT_PLAY_SMS_APPROVAL_REF is required for an approved OTP SMS release"
        }
        require(!(project.findProperty("CV_KEYSTORE") as String?).isNullOrBlank()) {
            "Owner release signing is required for an approved OTP SMS release"
        }
    }
}

tasks.register("buildApprovedOtpSmsRelay") {
    group = "build"
    description = "Build the Owner-approved, signed RECEIVE_SMS Runtime lane"
    dependsOn("assembleOtpSmsRelay")
}

// Every task that can emit the restricted APK/AAB is gated, including direct
// Gradle entry points. Lint, compilation and unit tests remain available to CI
// without signing credentials, but no RECEIVE_SMS installable artifact can be
// produced by bypassing the friendly buildApprovedOtpSmsRelay alias.
val restrictedOtpArtifactTasks = setOf(
    "assembleOtpSmsRelay",
    "bundleOtpSmsRelay",
    "packageOtpSmsRelay",
    "packageOtpSmsRelayBundle",
    "packageOtpSmsRelayUniversalApk",
    "signOtpSmsRelayBundle",
    "makeApkFromBundleForOtpSmsRelay",
    "zipApksForOtpSmsRelay",
    "extractApksForOtpSmsRelay",
    "extractApksFromBundleForOtpSmsRelay",
    "signingConfigWriterOtpSmsRelay",
)
tasks.configureEach {
    if (name in restrictedOtpArtifactTasks) {
        dependsOn(otpSmsApprovalGate)
    }
}

repositories { google(); mavenCentral() }

dependencies {
    implementation(project(":core"))   // the VEC-1-proven normalize/classify/secret-guard
    implementation(project(":ime-engine"))

    // Exact official SDK used only by the explicit, one-message SMS User
    // Consent fallback. It grants no READ_SMS/RECEIVE_SMS authority. Version,
    // artifact hashes and Android SDK license are frozen in the adjacent lock.
    implementation("com.google.android.gms:play-services-auth-api-phone:18.2.0")

    val room = "2.6.1"
    implementation("androidx.room:room-runtime:$room")
    implementation("androidx.room:room-ktx:$room")
    ksp("androidx.room:room-compiler:$room")   // KSP, not annotationProcessor (Kotlin)

    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.6")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.6")  // LocalLifecycleOwner for setup-status refresh
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation(platform("androidx.compose:compose-bom:2024.09.03"))
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.work:work-runtime-ktx:2.9.1")

    testImplementation("junit:junit:4.13.2")
    // Host-JVM tests run against mockable android.jar, where Android's org.json
    // methods are stubs. Keep the real JSON implementation test-only so sync
    // batching tests can exercise serialization without changing APK deps.
    testImplementation("org.json:json:20260522")

    // Compile the residual IME manual-QA scaffolds without running them.
    // Device/emulator execution remains an explicit Owner/manual QA gate.
    androidTestImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.test:core:1.6.1")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test:runner:1.6.2")
    androidTestImplementation("androidx.test.uiautomator:uiautomator:2.3.0")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.6.1")
}

val smsUserConsentAar = configurations.detachedConfiguration(
    dependencies.create("com.google.android.gms:play-services-auth-api-phone:18.2.0@aar"),
).apply { isTransitive = false }
val smsUserConsentPomArtifact = configurations.detachedConfiguration(
    dependencies.create("com.google.android.gms:play-services-auth-api-phone:18.2.0@pom"),
).apply { isTransitive = false }

val verifySmsUserConsentDependency = tasks.register("verifySmsUserConsentDependency") {
    group = "verification"
    description = "Fail closed unless the resolved SMS User Consent AAR and POM match the production lock"
    inputs.file(smsUserConsentLockFile)

    doLast {
        fun sha256(file: File): String {
            val digest = MessageDigest.getInstance("SHA-256")
            file.inputStream().buffered().use { input ->
                val buffer = ByteArray(64 * 1024)
                while (true) {
                    val count = input.read(buffer)
                    if (count < 0) break
                    digest.update(buffer, 0, count)
                }
            }
            return digest.digest().joinToString("") { byte -> "%02x".format(byte) }
        }

        fun verify(file: File, expectedSize: Long, expectedSha256: String, label: String) {
            check(file.length() == expectedSize) {
                "$label size mismatch: expected $expectedSize, got ${file.length()}"
            }
            val actualSha256 = sha256(file)
            check(actualSha256 == expectedSha256) {
                "$label SHA-256 mismatch: expected $expectedSha256, got $actualSha256"
            }
        }

        verify(
            smsUserConsentAar.singleFile,
            smsUserConsentAarSize,
            smsUserConsentAarSha256,
            "play-services-auth-api-phone AAR",
        )
        verify(
            smsUserConsentPomArtifact.singleFile,
            smsUserConsentPomSize,
            smsUserConsentPomSha256,
            "play-services-auth-api-phone POM",
        )
    }
}

// Normal Runtime builds and the standard Gradle check lifecycle both enforce
// the exact Google Maven bytes. The restricted RECEIVE_SMS flavor remains
// separately protected by otpSmsRelayApprovalGate and is never produced by CI.
tasks.matching {
    it.name in setOf("preDebugBuild", "preReleaseBuild", "preOtpSmsRelayBuild", "check")
}.configureEach {
    dependsOn(verifySmsUserConsentDependency)
}
