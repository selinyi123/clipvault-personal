plugins {
    java
}

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

tasks.withType<JavaCompile>().configureEach {
    options.encoding = "UTF-8"
    options.release.set(17)
}

tasks.register<JavaExec>("verifySessionContract") {
    group = "verification"
    description = "Runs the locked synthetic session-contract vectors."
    dependsOn(tasks.testClasses)
    classpath = sourceSets["test"].runtimeClasspath
    mainClass.set("com.clipvault.poc.engine.SessionContractVectorRunner")
    systemProperty(
        "clipvault.sessionVectors",
        layout.projectDirectory.file("../vectors/session-contract-vectors.tsv").asFile.absolutePath,
    )
}

tasks.register<JavaExec>("verifyAndroidImeSlice") {
    group = "verification"
    description = "Runs the JVM-only Android IME client vertical slice."
    dependsOn(tasks.testClasses)
    classpath = sourceSets["test"].runtimeClasspath
    mainClass.set("com.clipvault.poc.engine.AndroidImeSliceRunner")
    systemProperty(
        "clipvault.androidImeSliceVectors",
        layout.projectDirectory.file("../vectors/android-ime-slice-vectors.tsv").asFile.absolutePath,
    )
    systemProperty(
        "clipvault.foundationEngineAssertions",
        layout.projectDirectory.file("../vectors/foundation-engine-assertions.tsv").asFile.absolutePath,
    )
}

tasks.named("check") {
    dependsOn(
        tasks.named("verifySessionContract"),
        tasks.named("verifyAndroidImeSlice"),
    )
}
