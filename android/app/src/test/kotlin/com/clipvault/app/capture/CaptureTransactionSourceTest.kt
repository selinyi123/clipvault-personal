package com.clipvault.app.capture

import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files
import java.nio.file.Path

class CaptureTransactionSourceTest {
    private val sourcePath = Path.of(
        "src", "main", "kotlin", "com", "clipvault", "app", "capture", "Capture.kt",
    )

    @Test
    fun planningPrecedesTheWriterTransactionAndCommitEffectsStayAtomic() {
        val source = readSource()
        val planning = source.indexOf("val verdict = SecretGuard.scan(content)")
        val transaction = source.indexOf("return db.runInTransaction<Result>", planning)
        val helper = source.indexOf("private fun touchDuplicate")

        assertTrue("Secret Guard planning must stay outside the writer transaction", planning >= 0)
        assertTrue("new-clip commit must use a Room transaction", transaction > planning)
        assertTrue("duplicate helper must follow the capture transaction", helper > transaction)

        val transactionBody = source.substring(transaction, helper)
        assertTrue("transaction must perform a race-safe hash recheck", transactionBody.contains("val existing = db.clips().byHash(hash)"))
        assertTrue("transaction must inspect INSERT IGNORE's result", transactionBody.contains("val insertResult = db.clips().insert(clip)"))
        assertTrue("ignored inserts must be handled explicitly", transactionBody.contains("if (insertResult == -1L)"))
        assertTrue("public outbox append must stay in the same transaction", transactionBody.contains("plannedOutbox?.let(db.outbox()::append)"))
    }

    @Test
    fun ignoredInsertReturnsThePersistedWinnerBeforeAnyOutboxAppend() {
        val source = readSource()
        val transaction = source.indexOf("return db.runInTransaction<Result>", source.indexOf("val verdict ="))
        val ignored = source.indexOf("if (insertResult == -1L)", transaction)
        val winner = source.indexOf("val winner = checkNotNull(db.clips().byHash(hash))", ignored)
        val duplicateReturn = source.indexOf("return@runInTransaction touchDuplicate(db, winner, now)", winner)
        val outbox = source.indexOf("plannedOutbox?.let(db.outbox()::append)", duplicateReturn)

        assertTrue("INSERT IGNORE conflict path is missing", ignored >= transaction)
        assertTrue("conflict path must load the actual persisted clip", winner > ignored)
        assertTrue("conflict path must return the persisted duplicate", duplicateReturn > winner)
        assertTrue("conflict path must exit before an outbox row can be emitted", outbox > duplicateReturn)
    }

    private fun readSource(): String {
        assertTrue("source file is missing: $sourcePath", Files.isRegularFile(sourcePath))
        return String(Files.readAllBytes(sourcePath), Charsets.UTF_8)
    }
}
