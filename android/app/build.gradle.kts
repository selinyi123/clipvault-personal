plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "com.clipvault.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.clipvault.app"
        minSdk = 26          // Android 8.0: Quick Settings Tile + modern clipboard rules
        targetSdk = 34
        versionCode = 1
        versionName = "0.2"
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
    annotationProcessor("androidx.room:room-compiler:$room")

    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.6")
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation(platform("androidx.compose:compose-bom:2024.09.03"))
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.work:work-runtime-ktx:2.9.1")
}
