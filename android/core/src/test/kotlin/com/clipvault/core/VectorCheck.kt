package com.clipvault.core

import org.json.JSONArray
import java.io.File
import kotlin.system.exitProcess

/**
 * VEC-1 cross-platform conformance (CONTRACTS section 8). Loads the SAME
 * contracts vectors JSON files the Python suite uses and asserts the Kotlin
 * core produces identical results. The vectors are the single arbiter between
 * the two platforms. Shared by the JUnit test (VectorTest) and the CLI main.
 */
fun collectFailures(dir: String): Pair<Int, List<String>> {
    val failures = ArrayList<String>()

    val norm = JSONArray(File("$dir/normalization.json").readText())
    for (i in 0 until norm.length()) {
        val c = norm.getJSONObject(i)
        val raw = c.getString("raw")
        val got = Normalize.normalize(raw)
        if (got != c.getString("normalized")) failures.add("NORM normalize: ${raw.take(30)}")
        if (Normalize.contentHash(got) != c.getString("hash")) failures.add("NORM hash: ${raw.take(30)}")
    }

    val cls = JSONArray(File("$dir/classifier.json").readText())
    for (i in 0 until cls.length()) {
        val c = cls.getJSONObject(i)
        val got = Classifier.classify(c.getString("content"))
        if (got != c.getString("expected_type")) {
            failures.add("CLS '${c.getString("content").take(30)}': got=$got want=${c.getString("expected_type")}")
        }
    }

    val sg = JSONArray(File("$dir/secret_guard.json").readText())
    for (i in 0 until sg.length()) {
        val c = sg.getJSONObject(i)
        val v = SecretGuard.scan(c.getString("content"))
        val wantSecret = c.getBoolean("is_secret")
        val wantLevel = if (c.isNull("level")) null else c.getString("level")
        val reasonsArr = c.getJSONArray("reasons")
        val wantReasons = (0 until reasonsArr.length()).map { reasonsArr.getString(it) }.sorted()
        if (v.isSecret != wantSecret || v.level != wantLevel || v.reasons.sorted() != wantReasons) {
            failures.add("SG '${c.getString("content").take(30)}': got=(${v.isSecret},${v.level},${v.reasons}) want=($wantSecret,$wantLevel,$wantReasons)")
        }
    }

    return (norm.length() + cls.length() + sg.length()) to failures
}

fun main(args: Array<String>) {
    val dir = if (args.isNotEmpty()) args[0] else "../../contracts/vectors"
    val (total, failures) = collectFailures(dir)
    if (failures.isEmpty()) {
        println("VEC-1 OK: $total vectors passed")
        exitProcess(0)
    }
    println("VEC-1 FAILED: ${failures.size}/$total mismatches:")
    failures.forEach { println("  $it") }
    exitProcess(1)
}
