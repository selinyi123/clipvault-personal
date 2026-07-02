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

// :app compiles and runs host-JVM tests on Java 17. A Java 21 core jar loads in
// core's own test task but fails with UnsupportedClassVersionError when app
// tests execute shared SG-1 code, so the shared runtime boundary must match.
kotlin { jvmToolchain(17) }
