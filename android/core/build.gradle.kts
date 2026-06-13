// Pure Kotlin/JVM module — NO Android dependency, so VEC-1 cross-platform
// vector tests run with just a JDK + Gradle (no Android SDK needed).
plugins {
    id("org.jetbrains.kotlin.jvm")
}

repositories { mavenCentral() }

dependencies {
    testImplementation("org.json:json:20240303")
    testImplementation(kotlin("test"))
}

tasks.test {
    useJUnitPlatform()
    // The vectors live at the repo root; pass the path to the test.
    systemProperty("clipvault.vectors", "${rootDir.parent}/contracts/vectors")
}

kotlin { jvmToolchain(21) }
