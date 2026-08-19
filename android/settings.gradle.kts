pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "ClipVaultPersonal"
include(":core", ":ime-engine", ":rime-engine-android", ":ime-app", ":app")
