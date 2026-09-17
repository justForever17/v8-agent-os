package expo.modules.v8executor

import java.util.concurrent.atomic.AtomicBoolean

data class Viewport(val left: Int, val top: Int, val width: Int, val height: Int)
data class FrameGeometry(val width: Int, val height: Int, val rotation: Int, val viewport: Viewport) {
  init {
    require(width in 1..4096 && height in 1..4096 && width.toLong() * height <= 8_388_608) { "invalid_frame_geometry" }
    require(rotation in 0..3 && viewport.left >= 0 && viewport.top >= 0 && viewport.width in 1..16384 && viewport.height in 1..16384) { "invalid_viewport" }
  }
  fun displayPoint(x: Int, y: Int): Pair<Float, Float> {
    require(x in 0 until width && y in 0 until height) { "coordinate_out_of_bounds" }
    return (viewport.left + (x + 0.5f) * viewport.width / width) to
      (viewport.top + (y + 0.5f) * viewport.height / height)
  }
}

/** All async stages carry the same ticket. Revocation never reactivates a ticket. */
class CommandWork {
  @Volatile var active = true
    private set
  private val cleanup = mutableListOf<() -> Unit>()
  @Synchronized fun onCancel(action: () -> Unit) { if (active) cleanup.add(action) else action() }
  @Synchronized fun cancel() {
    if (!active) return
    active = false
    cleanup.toList().also { cleanup.clear() }.forEach { runCatching(it) }
  }
  @Synchronized fun complete() { active = false; cleanup.clear() }
}

/** Owns a platform callback buffer across a worker handoff, including rejected executors. */
class NativeCallbackResource<T>(val value: T, private val release: () -> Unit) {
  private val closed = AtomicBoolean(false)
  fun close() { if (closed.compareAndSet(false, true)) release() }
  fun deliver(work: CommandWork, valid: () -> Boolean, accept: (NativeCallbackResource<T>) -> Unit): Boolean {
    if (!work.active || !runCatching(valid).getOrDefault(false)) { close(); return false }
    try { accept(this); return true }
    catch (error: Exception) { close(); throw error }
  }
}

object CapturePolicy {
  fun authorize(api: Int, scope: String, localDisplay: Boolean, serverDisplay: Boolean) {
    require(scope == "window" || scope == "display") { "invalid_capture_scope" }
    if (scope == "window") require(api >= 34) { "requires_android_14_window_capture" }
    else require(localDisplay && serverDisplay) { "display_capture_not_authorized" }
  }
  fun gesture(api: Int, scope: String, observedMono: Long, nowMono: Long, current: Boolean) {
    require(api >= 34 && scope == "window") { "requires_window_capture" }
    require(current) { "stale_frame" }
    require(nowMono >= observedMono && nowMono - observedMono <= 10_000) { "observation_expired" }
  }
}
