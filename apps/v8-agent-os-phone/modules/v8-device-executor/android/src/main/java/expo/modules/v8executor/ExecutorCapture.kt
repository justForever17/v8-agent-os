package expo.modules.v8executor

import android.accessibilityservice.AccessibilityService
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.ColorSpace
import android.graphics.Paint
import android.graphics.Rect
import android.os.Build
import android.os.SystemClock
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.OutputStream
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit

/** One screenshot at a time; pixels stay native, are bounded, and are never cached on disk. */
class ExecutorCapture(private val service: ExecutorAccessibilityService) {
  private val compression = ThreadPoolExecutor(1, 1, 0, TimeUnit.MILLISECONDS, ArrayBlockingQueue(1),
    { work -> Thread(work, "v8-executor-jpeg").apply { isDaemon = true } }, ThreadPoolExecutor.AbortPolicy())
  private var lastCaptureMono = -1_000L
  data class Image(val observation: JSONObject, val jpeg: ByteArray, val target: ExecutorAccessibilityService.CaptureTarget)

  fun capture(target: ExecutorAccessibilityService.CaptureTarget, work: CommandWork, valid: () -> Boolean,
              callback: (Result<Image>) -> Unit) {
    val start = SystemClock.elapsedRealtime()
    require(start - lastCaptureMono >= 350) { "capture_rate_limited" }
    lastCaptureMono = start
    request(target, work, valid, { resource ->
      val screenshot = resource.value
      val age = SystemClock.uptimeMillis() - screenshot.timestamp
      val capturedTarget = target.copy(observedUnixMs = System.currentTimeMillis() - age, observedMono = SystemClock.elapsedRealtime() - age)
      // Ownership of the HardwareBuffer transfers exactly once to the worker.
      compression.execute {
        val result = runCatching {
          require(work.active) { "capture_cancelled" }
          val buffer = screenshot.hardwareBuffer
          require(buffer.width in 1..8192 && buffer.height in 1..8192 && buffer.width.toLong() * buffer.height <= 8_388_608) { "capture_dimensions_exceeded" }
          require(buffer.width == target.viewport.width && buffer.height == target.viewport.height) { "capture_geometry_mismatch" }
          val hardware = Bitmap.wrapHardwareBuffer(buffer, screenshot.colorSpace) ?: error("capture_bitmap_unavailable")
          try {
            val maxEdge = maxOf(hardware.width, hardware.height)
            val scale = minOf(1.0, 1600.0 / maxEdge)
            val width = maxOf(1, (hardware.width * scale).toInt())
            val height = maxOf(1, (hardware.height * scale).toInt())
            // A software Canvas cannot draw a hardware Bitmap directly. Keep
            // its required source copy bounded to 8 Mi pixels, then convert the
            // transmitted bitmap explicitly to sRGB without screenshot metadata.
            val source = hardware.copy(Bitmap.Config.ARGB_8888, false) ?: error("capture_bitmap_unavailable")
            try {
              val software = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888, false, ColorSpace.get(ColorSpace.Named.SRGB))
              try {
                Canvas(software).drawBitmap(source, null, Rect(0, 0, width, height), Paint(Paint.FILTER_BITMAP_FLAG))
                val output = LimitedOutput(MAX_BYTES, work)
                require(software.compress(Bitmap.CompressFormat.JPEG, 80, output)) { "jpeg_encoding_failed" }
                val jpeg = output.bytes()
                require(work.active && jpeg.isNotEmpty()) { "capture_cancelled" }
                val hash = MessageDigest.getInstance("SHA-256").digest(jpeg).joinToString("") { "%02x".format(it) }
                val frameId = UUID.randomUUID().toString()
                val geometry = FrameGeometry(width, height, target.rotation, target.viewport)
                val observation = service.captureObservation(capturedTarget, geometry, frameId, hash)
                Image(observation, jpeg, capturedTarget)
              } finally { software.recycle() }
            } finally { source.recycle() }
          } finally { hardware.recycle() }
        }
        resource.close()
        try {
          service.mainExecutor.execute {
            if (!work.active || !runCatching(valid).getOrDefault(false)) { result.getOrNull()?.jpeg?.fill(0); return@execute }
            val checked = result.mapCatching { image -> service.validateCaptureTarget(target); image }
            if (checked.isFailure) result.getOrNull()?.jpeg?.fill(0)
            callback(checked)
          }
        } catch (_: Exception) { result.getOrNull()?.jpeg?.fill(0) }
      }
    }, { callback(Result.failure(IllegalStateException(it))) })
  }

  /** No pixels are encoded or uploaded for the pre-gesture FLAG_SECURE check. */
  fun secureProbe(target: ExecutorAccessibilityService.CaptureTarget, work: CommandWork, valid: () -> Boolean,
                  callback: (Result<Unit>) -> Unit) {
    require(Build.VERSION.SDK_INT >= 34 && target.scope == "window") { "requires_window_capture" }
    request(target, work, valid, { resource ->
      resource.close()
      callback(runCatching { service.validateCaptureTarget(target) })
    }, { callback(Result.failure(IllegalStateException(it))) })
  }

  private fun request(target: ExecutorAccessibilityService.CaptureTarget, work: CommandWork, valid: () -> Boolean,
                      success: (NativeCallbackResource<AccessibilityService.ScreenshotResult>) -> Unit, failure: (String) -> Unit) {
    require(work.active && valid()) { "capture_cancelled" }
    service.validateCaptureTarget(target)
    val requestedUptime = SystemClock.uptimeMillis()
    val callback = object : AccessibilityService.TakeScreenshotCallback {
      override fun onSuccess(result: AccessibilityService.ScreenshotResult) {
        val resource = NativeCallbackResource(result) { result.hardwareBuffer.close() }
        try {
          resource.deliver(work, valid) {
            require(result.timestamp >= requestedUptime && result.timestamp <= SystemClock.uptimeMillis()) { "stale_capture" }
            service.validateCaptureTarget(target)
            success(it)
          }
        } catch (_: Exception) { if (work.active) failure("capture_unavailable") }
      }
      override fun onFailure(errorCode: Int) {
        if (!work.active || !runCatching(valid).getOrDefault(false)) return
        failure(when (errorCode) {
          AccessibilityService.ERROR_TAKE_SCREENSHOT_SECURE_WINDOW -> "secure_window"
          AccessibilityService.ERROR_TAKE_SCREENSHOT_INTERVAL_TIME_SHORT -> "capture_rate_limited"
          AccessibilityService.ERROR_TAKE_SCREENSHOT_NO_ACCESSIBILITY_ACCESS -> "accessibility_revoked"
          else -> "capture_unavailable"
        })
      }
    }
    if (target.scope == "window" && Build.VERSION.SDK_INT >= 34) service.takeScreenshotOfWindow(target.windowId, service.mainExecutor, callback)
    else service.takeScreenshot(target.displayId, service.mainExecutor, callback)
  }

  fun close() { compression.shutdown() }
  private class LimitedOutput(private val limit: Int, private val work: CommandWork) : OutputStream() {
    private val output = ByteArrayOutputStream()
    override fun write(value: Int) { require(work.active && output.size() < limit) { "capture_bytes_exceeded" }; output.write(value) }
    override fun write(bytes: ByteArray, offset: Int, length: Int) {
      require(work.active && output.size().toLong() + length <= limit) { "capture_bytes_exceeded" }
      output.write(bytes, offset, length)
    }
    fun bytes() = output.toByteArray()
  }
  companion object { const val MAX_BYTES = 2_097_152 }
}
