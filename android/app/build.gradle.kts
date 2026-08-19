plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.google.devtools.ksp")
}

android {
    namespace = "com.clipvault.app"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.clipvault.app"
        minSdk = 26          // Android 8.0: Quick Settings Tile + modern clipboard rules
        targetSdk = 36
        versionCode = 13
        versionName = "1.6.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        buildConfigField("boolean", "OTP_SMS_CAPTURE_INCLUDED", "false")
    }
    // Release signing reads from -P properties (or ~/.gradle), so the keystore
    // and passwords never live in the repo. Falls back gracefully when unset.
    signingConfigs {
        create("release") {
            val ksPath = (project.findProperty("CV_KEYSTORE") as String?)
            if (ksPath != null) {
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
            if (project.findProperty("CV_KEYSTORE") != null) {
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
        require(project.findProperty("CV_KEYSTORE") != null) {
            "Owner release signing is required for an approved OTP SMS release"
        }
    }
}

tasks.register("buildApprovedOtpSmsRelay") {
    group = "build"
    description = "Build the Owner-approved, signed RECEIVE_SMS Runtime lane"
    dependsOn("assembleOtpSmsRelay")
}

val approvedOtpSmsBuildRequested = gradle.startParameter.taskNames.any {
    it.substringAfterLast(':') == "buildApprovedOtpSmsRelay"
}
if (approvedOtpSmsBuildRequested) {
    tasks.configureEach {
        if (name == "preOtpSmsRelayBuild") dependsOn(otpSmsApprovalGate)
    }
}

repositories { google(); mavenCentral() }

dependencies {
    implementation(project(":core"))   // the VEC-1-proven normalize/classify/secret-guard
    implementation(project(":ime-engine"))
    implementation(project(":rime-engine-android"))

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
