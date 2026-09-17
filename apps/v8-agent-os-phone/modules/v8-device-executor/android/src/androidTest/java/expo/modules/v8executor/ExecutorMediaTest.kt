package expo.modules.v8executor

import androidx.test.platform.app.InstrumentationRegistry
import okhttp3.*
import okhttp3.ResponseBody.Companion.toResponseBody
import okhttp3.MediaType.Companion.toMediaType
import okio.Buffer
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicReference

/** Real OkHttp request bodies/main callbacks; no network, device capture or gesture dispatch. */
class ExecutorMediaTest {
  private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
  private fun image(): ExecutorCapture.Image {
    val frame = JSONObject().put("frameId", "frame-1").put("sha256", "a".repeat(64)).put("width", 10).put("height", 20).put("mimeType", "image/jpeg")
    return ExecutorCapture.Image(JSONObject().put("frame", frame), byteArrayOf(1, 2, 3, 4),
      ExecutorAccessibilityService.CaptureTarget("fixture.app", 8, 0, 0, Viewport(0, 0, 10, 20), Viewport(0, 0, 10, 20),
        "window", 3, "window-signature", "{}", "observation-1", 1, 1))
  }
  private fun command() = JSONObject().put("commandId", "fixture-command").put("commandDigest", "b".repeat(64))
  private fun reservation(path: String = "/api/executor/media/media-1") = JSONObject().put("mediaId", "media-1").put("uploadPath", path)
    .put("maxBytes", 2097152).put("expiresUnixMs", System.currentTimeMillis() + 10000).toString()
  private fun response(request: Request, body: String) = Response.Builder().request(request).protocol(Protocol.HTTP_1_1).code(200).message("OK")
    .body(body.toResponseBody("application/json".toMediaType())).build()

  @Test fun pixelsUseSeparateBoundedPutAndMetadataUsesNoBase64() {
    val seen = CopyOnWriteArrayList<String>()
    val client = OkHttpClient.Builder().addInterceptor { chain ->
      val request = chain.request(); seen.add(request.method)
      assertEquals("engine.invalid", request.url.host)
      val body = Buffer(); request.body!!.writeTo(body)
      if (request.method == "POST") {
        val metadata = JSONObject(body.readUtf8())
        assertEquals(4, metadata.getInt("byteLength")); assertFalse(metadata.has("base64"))
        response(request, reservation())
      } else {
        assertEquals("PUT", request.method); assertEquals("/api/executor/media/media-1", request.url.encodedPath)
        assertEquals("image/jpeg", request.body!!.contentType().toString()); assertArrayEquals(byteArrayOf(1, 2, 3, 4), body.readByteArray())
        response(request, """{"mediaId":"media-1","frameId":"frame-1","sha256":"${"a".repeat(64)}","width":10,"height":20,"status":"uploaded"}""")
      }
    }.build()
    val done = CountDownLatch(1); val result = AtomicReference<Result<ExecutorCapture.Image>>()
    val work = CommandWork(); val image = image()
    instrumentation.runOnMainSync {
      ExecutorMediaUploader(client, "https://engine.invalid", "synthetic-test-credential").upload(command(), image, work, { work.active }) { result.set(it); done.countDown() }
    }
    assertTrue(done.await(5, TimeUnit.SECONDS)); assertTrue(result.get().isSuccess)
    assertEquals(listOf("POST", "PUT"), seen)
    assertEquals("media-1", result.get().getOrThrow().observation.getJSONObject("frame").getString("mediaId"))
    assertArrayEquals(ByteArray(4), image.jpeg)
    work.complete()
  }
  @Test fun aStopBeforeLateReservationCancelsUploadAndDeletesOnlyItsUnpublishedHandle() {
    val reserved = CountDownLatch(1); val release = CountDownLatch(1); val deleted = CountDownLatch(1)
    val seen = CopyOnWriteArrayList<String>()
    val client = OkHttpClient.Builder().addInterceptor { chain ->
      val request = chain.request(); seen.add(request.method)
      if (request.method == "POST") {
        reserved.countDown(); assertTrue(release.await(5, TimeUnit.SECONDS)); response(request, reservation())
      } else { assertEquals("DELETE", request.method); deleted.countDown(); response(request, "{}") }
    }.build()
    val work = CommandWork(); val image = image()
    instrumentation.runOnMainSync {
      ExecutorMediaUploader(client, "https://engine.invalid", "synthetic-test-credential").upload(command(), image, work, { work.active }) { fail("A cancelled capture must not publish") }
    }
    assertTrue(reserved.await(5, TimeUnit.SECONDS))
    instrumentation.runOnMainSync { work.cancel() }
    release.countDown()
    // Cancellation may close the reservation before its handle reaches native;
    // the server TTL handles that case. Either way, no PUT or receipt is possible.
    deleted.await(200, TimeUnit.MILLISECONDS)
    assertFalse(seen.contains("PUT")); assertArrayEquals(ByteArray(4), image.jpeg)
  }
  @Test fun aServerSuppliedForeignUploadPathNeverReceivesCredentialsOrPixels() {
    val seen = CopyOnWriteArrayList<String>()
    val client = OkHttpClient.Builder().addInterceptor { chain ->
      seen.add(chain.request().url.host); response(chain.request(), reservation("https://other.invalid/steal"))
    }.build()
    val work = CommandWork(); val done = CountDownLatch(1)
    val result = AtomicReference<Result<ExecutorCapture.Image>>()
    instrumentation.runOnMainSync {
      ExecutorMediaUploader(client, "https://engine.invalid", "synthetic-test-credential").upload(command(), image(), work, { true }) { result.set(it); done.countDown() }
    }
    assertTrue(done.await(5, TimeUnit.SECONDS)); assertTrue(result.get().isFailure)
    assertTrue(seen.isNotEmpty()); assertTrue(seen.all { it == "engine.invalid" }); work.complete()
  }
}
