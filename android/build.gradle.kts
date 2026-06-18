// Root build file. Plugin versions are declared here and applied per-module.
plugins {
    id("com.android.application") version "8.5.2" apply false
    id("org.jetbrains.kotlin.android") version "2.0.21" apply false
    id("org.jetbrains.kotlin.jvm") version "2.0.21" apply false
    // Kotlin 2.0+ requires the Compose Compiler plugin when Compose is enabled.
    id("org.jetbrains.kotlin.plugin.compose") version "2.0.21" apply false
    // KSP runs Room's annotation processor (annotationProcessor is a no-op for Kotlin —
    // without this, Room's AppDatabase_Impl is never generated and the app crashes at startup).
    id("com.google.devtools.ksp") version "2.0.21-1.0.25" apply false
}
