package com.clipvault.imeapp

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BackspaceRepeatControllerTest {
    @Test
    fun pressDeletesImmediatelyThenRepeatsUntilRelease() {
        val scheduler = FakeScheduler()
        var deletes = 0
        val controller = BackspaceRepeatController(scheduler, { deletes += 1; true })

        assertTrue(controller.press())
        assertEquals(1, deletes)
        assertEquals(360L, scheduler.delay)

        scheduler.fire()
        assertEquals(2, deletes)
        assertEquals(55L, scheduler.delay)

        controller.release()
        scheduler.fire()
        assertEquals(2, deletes)
        assertTrue(scheduler.cancelled)
    }

    @Test
    fun duplicateDownDoesNotCreateAnotherRepeatChain() {
        val scheduler = FakeScheduler()
        var deletes = 0
        val controller = BackspaceRepeatController(scheduler, { deletes += 1; true })

        assertTrue(controller.press())
        assertFalse(controller.press())
        assertEquals(1, deletes)
    }

    @Test
    fun onePressHasAnAbsoluteSafetyBound() {
        val scheduler = FakeScheduler()
        var deletes = 0
        val controller = BackspaceRepeatController(
            scheduler = scheduler,
            deleteOnce = { deletes += 1; true },
            maximumDeletesPerPress = 3,
        )

        assertTrue(controller.press())
        scheduler.fire()
        scheduler.fire()
        scheduler.fire()

        assertEquals(3, deletes)
        assertFalse(scheduler.hasPending)
    }

    private class FakeScheduler : RepeatScheduler {
        var delay: Long? = null
        var cancelled = false
        private var action: (() -> Unit)? = null
        val hasPending: Boolean get() = action != null

        override fun schedule(delayMs: Long, action: () -> Unit): RepeatCancellation {
            delay = delayMs
            this.action = action
            cancelled = false
            return RepeatCancellation {
                cancelled = true
                if (this.action === action) this.action = null
            }
        }

        fun fire() {
            val current = action ?: return
            action = null
            current()
        }
    }
}
