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
        versionCode = 12
        versionName = "1.5.16"
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
