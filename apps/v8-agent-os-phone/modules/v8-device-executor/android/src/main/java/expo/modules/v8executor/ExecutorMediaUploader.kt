package expo.modules.v8executor

import android.os.Handler
import android.os.Looper
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import okio.BufferedSink
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.atomic.AtomicBoolean

/** Media is uploaded only to this binding's exact HTTPS paths, outside WSS/RN. */
class ExecutorMediaUploader(private val client: OkHttpClient, private val baseUrl: String, private val credential: String) {
  private val main = Handler(Looper.getMainLooper())
  fun upload(command: JSONObject, image: ExecutorCapture.Image, work: CommandWork, valid: () -> Boolean,
             callback: (Result<ExecutorCapture.Image>) -> Unit) {
    require(Looper.myLooper() == Looper.getMainLooper())
    require(work.active && valid()) { "capture_cancelled" }
    require(baseUrl.startsWith("https://") && image.jpeg.size in 1..ExecutorCapture.MAX_BYTES) { "invalid_media_upload" }
    val frame = image.observation.getJSONObject("frame")
    val reserve = JSONObject().put("commandId", command.getString("commandId")).put("commandDigest", command.getString("commandDigest"))
      .put("observation", image.observation).put("byteLength", image.jpeg.size).put("sha256", frame.getString("sha256")).put("mimeType", "image/jpeg")
    work.onCancel { image.jpeg.fill(0) }
    json(request("/api/executor/media").post(reserve.toString().toRequestBody(JSON)).build(), work) { reservation ->
      val mediaId = reservation.getOrNull()?.optString("mediaId")?.takeIf { it.matches(Regex("[A-Za-z0-9_-]{1,128}")) }
      val discarded = AtomicBoolean(false)
      fun discardReservation() { if (mediaId != null && discarded.compareAndSet(false, true)) discard(mediaId) }
      if (mediaId != null) work.onCancel(::discardReservation)
      if (!work.active || !valid()) { work.cancel(); return@json }
      val result = reservation.mapCatching {
        require(mediaId != null) { "invalid_media_reservation" }
        val path = "/api/executor/media/$mediaId"
        require(it.getString("uploadPath") == path && it.getLong("expiresUnixMs") > System.currentTimeMillis() &&
          it.getLong("maxBytes") in image.jpeg.size.toLong()..ExecutorCapture.MAX_BYTES.toLong()) { "invalid_media_reservation" }
        path
      }
      if (result.isFailure) { discardReservation(); image.jpeg.fill(0); callback(Result.failure(result.exceptionOrNull()!!)); return@json }
      val body = object : RequestBody() {
        override fun contentType() = JPEG
        override fun contentLength() = image.jpeg.size.toLong()
        override fun writeTo(sink: BufferedSink) {
          var offset = 0
          while (offset < image.jpeg.size) {
            if (!work.active) throw IOException("capture_cancelled")
            val count = minOf(8192, image.jpeg.size - offset)
            sink.write(image.jpeg, offset, count); offset += count
          }
        }
      }
      // The validity function rechecks the current app/window/scope immediately
      // before each HTTP stage and again before any observation can be published.
      if (!valid()) { work.cancel(); return@json }
      json(request(result.getOrThrow()).put(body).build(), work) { uploaded ->
        image.jpeg.fill(0)
        if (!work.active || !valid()) { work.cancel(); return@json }
        val checked = uploaded.mapCatching {
          require(it.getString("mediaId") == mediaId && it.getString("frameId") == frame.getString("frameId") &&
            it.getString("sha256") == frame.getString("sha256") && it.getInt("width") == frame.getInt("width") &&
            it.getInt("height") == frame.getInt("height") && it.getString("status") == "uploaded") { "media_result_mismatch" }
          frame.put("mediaId", mediaId)
          image
        }
        if (checked.isFailure) discardReservation()
        callback(checked)
      }
    }
  }
  private fun request(path: String) = Request.Builder().url(baseUrl + path).header("Authorization", "Bearer $credential")
  private fun json(request: Request, work: CommandWork, callback: (Result<JSONObject>) -> Unit) {
    val call = client.newCall(request)
    work.onCancel { call.cancel() }
    if (!work.active) return
    call.enqueue(object : Callback {
      override fun onFailure(call: Call, e: IOException) { main.post { callback(Result.failure(IllegalStateException("media_transport_failed"))) } }
      override fun onResponse(call: Call, response: Response) {
        val result = runCatching {
          response.use {
            require(it.isSuccessful) { "media_upload_rejected" }
            val body = it.body ?: error("media_empty_response")
            val source = body.source(); source.request((ExecutorWire.MAX_BYTES + 1).toLong())
            require(source.buffer.size <= ExecutorWire.MAX_BYTES) { "media_response_too_large" }
            ExecutorWire.parse(source.readUtf8())
          }
        }
        main.post { callback(result) }
      }
    })
  }
  private fun discard(mediaId: String) {
    client.newCall(request("/api/executor/media/$mediaId").delete().build()).enqueue(object : Callback {
      override fun onFailure(call: Call, e: IOException) = Unit // Server TTL also reaps unpublished media.
      override fun onResponse(call: Call, response: Response) { response.close() }
    })
  }
  companion object {
    private val JSON = "application/json".toMediaType()
    private val JPEG = "image/jpeg".toMediaType()
  }
}
