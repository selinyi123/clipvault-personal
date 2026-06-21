plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.google.devtools.ksp")
}

android {
    namespace = "com.clipvault.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.clipvault.app"
        minSdk = 26          // Android 8.0: Quick Settings Tile + modern clipboard rules
        targetSdk = 34
        versionCode = 8
        versionName = "1.5.0"
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
    }

    buildFeatures { compose = true }   // compiler managed by kotlin.plugin.compose
    kotlinOptions { jvmTarget = "17" }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

repositories { google(); mavenCentral() }

dependencies {
    implementation(project(":core"))   // the VEC-1-proven normalize/classify/secret-guard

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
}
