package expo.modules.v8executor

import org.junit.Assert.*
import org.junit.Test

class FramePolicyTest {
  @Test fun overlayFreeScreenshotDoesNotAuthorizeTappingOrCrossingItsScreenOverlay() {
    val overlay = listOf(Viewport(900, 1700, 180, 180))
    assertTrue(CapturePolicy.pathUnobstructed(400f to 800f, 600f to 800f, overlay))
    assertFalse(CapturePolicy.pathUnobstructed(950f to 1750f, 950f to 1750f, overlay))
    assertFalse(CapturePolicy.pathUnobstructed(900f to 1700f, 900f to 1700f, overlay))
    assertTrue(CapturePolicy.pathUnobstructed(1080f to 1880f, 1080f to 1880f, overlay))
    assertFalse(CapturePolicy.pathUnobstructed(850f to 1750f, 1079f to 1750f, overlay))
    assertFalse(CapturePolicy.pathUnobstructed(1000f to 1600f, 1000f to 1950f, overlay))
    assertTrue(CapturePolicy.pathUnobstructed(800f to 1600f, 899f to 1699f, overlay))
  }
  @Test fun displayCaptureRequiresBothIndependentPermissionsAtEveryApiLevel() {
    for (api in listOf(30, 33, 34, 36)) {
      for ((local, server) in listOf(false to false, true to false, false to true)) {
        assertThrows(IllegalArgumentException::class.java) { CapturePolicy.authorize(api, "display", local, server) }
      }
      CapturePolicy.authorize(api, "display", true, true)
    }
    assertThrows(IllegalArgumentException::class.java) { CapturePolicy.authorize(33, "window", true, true) }
    CapturePolicy.authorize(34, "window", false, false)
  }
  @Test fun oldAndroidDisplayFramesAndStaleFramesNeverAdmitGestures() {
    for (api in listOf(30, 33)) assertThrows(IllegalArgumentException::class.java) { CapturePolicy.gesture(api, "window", 100, 101, true) }
    assertThrows(IllegalArgumentException::class.java) { CapturePolicy.gesture(36, "display", 100, 101, true) }
    assertThrows(IllegalArgumentException::class.java) { CapturePolicy.gesture(34, "window", 100, 101, false) }
    assertThrows(IllegalArgumentException::class.java) { CapturePolicy.gesture(34, "window", 100, 10_101, true) }
    assertThrows(IllegalArgumentException::class.java) { CapturePolicy.gesture(34, "window", 100, 99, true) }
    CapturePolicy.gesture(34, "window", 100, 101, true)
  }
  @Test fun resizedWindowPixelsMapToTheirActualOffsetWithoutARotationGuess() {
    val frame = FrameGeometry(400, 800, 1, Viewport(120, 70, 800, 1600))
    assertEquals(121f to 71f, frame.displayPoint(0, 0))
    assertEquals(919f to 1669f, frame.displayPoint(399, 799))
    listOf(-1 to 0, 0 to -1, 400 to 0, 0 to 800, Int.MAX_VALUE to 0).forEach { (x, y) ->
      assertThrows(IllegalArgumentException::class.java) { frame.displayPoint(x, y) }
    }
    assertThrows(IllegalArgumentException::class.java) { FrameGeometry(4096, 4096, 0, Viewport(0, 0, 4096, 4096)) }
  }
  @Test fun stopRevocationAndNewSessionCannotReviveLateCallbacks() {
    val old = CommandWork(); var uploads = 0; var dispatches = 0; var cancelled = 0
    old.onCancel { cancelled++ }
    val lateCompression = { if (old.active) uploads++ }
    val lateProbe = { if (old.active) dispatches++ }
    old.cancel()
    val fresh = CommandWork()
    lateCompression(); lateProbe(); old.cancel()
    assertEquals(0, uploads); assertEquals(0, dispatches); assertEquals(1, cancelled)
    assertTrue(fresh.active); assertFalse(old.active)
    old.onCancel { cancelled++ }; assertEquals(2, cancelled)
  }
  @Test fun committedReceiptDoesNotDeletePublishedMediaButFailedWorkDoes() {
    var deletes = 0
    val success = CommandWork(); success.onCancel { deletes++ }; success.complete(); success.cancel()
    assertEquals(0, deletes)
    val failed = CommandWork(); failed.onCancel { deletes++ }; failed.cancel()
    assertEquals(1, deletes)
  }
  @Test fun aFailedCleanupCannotKeepAnUploadOrLaterCleanupAlive() {
    val work = CommandWork(); var socketClosed = false; var bytesCleared = false
    work.onCancel { throw IllegalStateException("synthetic_journal_failure") }
    work.onCancel { socketClosed = true }; work.onCancel { bytesCleared = true }
    work.cancel()
    assertFalse(work.active); assertTrue(socketClosed); assertTrue(bytesCleared)
  }
  @Test fun platformBufferClosesWhenValidityThrowsOrWorkerRejectsAndClosesOnlyOnce() {
    var releases = 0; var accepted = 0
    val work = CommandWork()
    val lostJournal = NativeCallbackResource("buffer") { releases++ }
    assertFalse(lostJournal.deliver(work, { throw IllegalStateException("journal_failed") }) { accepted++ })
    val shutdownWorker = NativeCallbackResource("buffer") { releases++ }
    assertThrows(java.util.concurrent.RejectedExecutionException::class.java) {
      shutdownWorker.deliver(work, { true }) { throw java.util.concurrent.RejectedExecutionException() }
    }
    shutdownWorker.close(); lostJournal.close()
    assertEquals(2, releases); assertEquals(0, accepted)
    val late = NativeCallbackResource("buffer") { releases++ }
    work.cancel(); assertFalse(late.deliver(work, { true }) { accepted++ })
    assertEquals(3, releases); assertEquals(0, accepted)
  }
}
