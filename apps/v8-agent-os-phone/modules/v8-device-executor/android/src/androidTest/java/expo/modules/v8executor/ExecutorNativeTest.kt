package expo.modules.v8executor

import android.content.Context
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.FutureTask
import java.util.concurrent.TimeUnit
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody

class ExecutorNativeTest {
  @Test fun lateRevocationCannotClearOrChangeAReplacementBinding() {
    val instrumentation = InstrumentationRegistry.getInstrumentation()
    val context = instrumentation.targetContext
    // A failed old request must not change the new status either. This case also
    // kills the old deviceId-only implementation without deleting any Keystore key.
    for (status in listOf(503, 401)) {
      val name = "executor-revoke-${UUID.randomUUID()}"
      val store = ExecutorStore(context, name)
      val controller = ExecutorController::class.java.getDeclaredConstructor(Context::class.java)
        .apply { isAccessible = true }.newInstance(context)
      fun field(name: String) = ExecutorController::class.java.getDeclaredField(name).apply { isAccessible = true }
      (field("store").get(controller) as ExecutorStore).close()
      field("store").set(controller, store)
      val entered = CountDownLatch(1)
      val release = CountDownLatch(1)
      val client = OkHttpClient.Builder().addInterceptor { chain ->
        assertEquals("old-authority.invalid", chain.request().url.host)
        assertEquals("Bearer synthetic-old-credential", chain.request().header("Authorization"))
        entered.countDown()
        check(release.await(10, TimeUnit.SECONDS))
        Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(status).message("fixture")
          .body("""{"code":"executor_credential_revoked"}""".toResponseBody()).build()
      }.build()
      field("client").set(controller, client)
      val oldBinding = JSONObject().put("authorityId", "old-authority").put("deviceId", "same-device-id")
        .put("baseUrl", "https://old-authority.invalid")
      val newBinding = JSONObject().put("authorityId", "new-authority").put("deviceId", "same-device-id")
        .put("baseUrl", "https://new-authority.invalid")
      val revoke = FutureTask { controller.revoke() }
      val worker = Thread(revoke, "fixture-revoke")
      try {
        store.saveCredential("synthetic-old-credential")
        instrumentation.runOnMainSync {
          field("binding").set(controller, oldBinding)
          field("guard").set(controller, null)
          store.saveConfig(oldBinding)
        }
        worker.start()
        assertTrue(entered.await(10, TimeUnit.SECONDS))
        instrumentation.runOnMainSync {
          store.saveCredential("synthetic-new-credential")
          store.saveConfig(newBinding)
          field("binding").set(controller, newBinding)
          field("lastError").set(controller, "requires_local_resume")
        }
        release.countDown()
        assertEquals(status == 401, revoke.get(10, TimeUnit.SECONDS))
        instrumentation.runOnMainSync {
          assertSame(newBinding, field("binding").get(controller))
          assertEquals("requires_local_resume", controller.state()["lastError"])
          assertEquals("new-authority", store.config()!!.getString("authorityId"))
          assertEquals("synthetic-new-credential", store.credential())
        }
      } finally {
        release.countDown()
        worker.join(11000)
        client.dispatcher.executorService.shutdown()
        client.connectionPool.evictAll()
        store.close()
        context.deleteDatabase("$name.db")
        context.deleteSharedPreferences(name)
      }
    }
  }
  @Test fun anAlreadyRevokedCredentialCanBeForgottenButOtherFailuresStayPending() {
    assertTrue(ExecutorWire.revocationConfirmed(200, "{}"))
    assertTrue(ExecutorWire.revocationConfirmed(401, """{"ok":false,"code":"executor_credential_revoked"}"""))
    for ((status, body) in listOf(401 to """{"code":"executor_credential_required"}""",
        403 to """{"code":"executor_credential_revoked"}""", 503 to "{}", 401 to "<html>error</html>",
        401 to """{"code":"executor_credential_revoked","code":"other"}""",
        401 to (" ".repeat(17000) + """{"code":"executor_credential_revoked"}"""))) {
      assertFalse(ExecutorWire.revocationConfirmed(status, body))
    }
  }
  @Test fun strictWireRejectsAmbiguousAndOversizedFrames() {
    listOf("{\"type\":\"query\",\"type\":\"command\"}", "{\"x\":NaN}", "{\"x\":1.5}", "{\"x\":01}",
      "{\"x\":" + "[".repeat(14) + "0" + "]".repeat(14) + "}", "{\"x\":\"" + "x".repeat(17000) + "\"}")
      .forEach { invalid -> assertThrows(Exception::class.java) { ExecutorWire.parse(invalid) } }
    assertEquals("{\"a\":\"测试\\n\",\"b\":2}", ExecutorWire.canonical(ExecutorWire.parse("{\"b\":2,\"a\":\"测试\\n\"}")))
  }
  @Test fun interruptedJournalRecoversWithoutReexecutingAndPreservesTerminalResults() {
    val context = InstrumentationRegistry.getInstrumentation().targetContext
    val name = "executor-test-${UUID.randomUUID()}"
    val store = ExecutorStore(context, name)
    try {
      for ((id, status) in listOf("before-apply" to "received", "during-apply" to "started", "done" to "succeeded")) {
        store.put(JSONObject().put("commandId", id).put("commandDigest", "digest-$id").put("status", status).put("receiptSeq", 1))
      }
      store.close()
      ExecutorStore(context, name).use { restarted ->
        restarted.recover()
        assertEquals("cancelled", restarted.get("before-apply")!!.getString("status"))
        assertEquals("unknown_outcome", restarted.get("during-apply")!!.getString("status"))
        assertEquals("succeeded", restarted.get("done")!!.getString("status"))
        assertEquals(2, restarted.get("during-apply")!!.getInt("receiptSeq"))
        assertTrue(restarted.recover().isEmpty())
      }
    } finally { store.close(); context.deleteDatabase("$name.db") }
  }
  @Test fun receiptQueriesCannotCrossAuthorityOrDeviceEvenForTheSameCommandId() {
    val context = InstrumentationRegistry.getInstrumentation().targetContext
    val name = "executor-test-${UUID.randomUUID()}"
    try {
      ExecutorStore(context, name).use { store ->
        for (authority in listOf("A", "B")) store.put(JSONObject().put("commandId", "same-id").put("authorityId", authority)
          .put("deviceId", "phone").put("commandDigest", "digest-$authority").put("status", "succeeded").put("receiptSeq", 1))
        assertEquals("digest-A", store.get("same-id", "A", "phone")!!.getString("commandDigest"))
        assertEquals("digest-B", store.get("same-id", "B", "phone")!!.getString("commandDigest"))
        assertNull(store.get("same-id", "A", "other-phone"))
        assertNull(store.get("same-id"))
      }
    } finally { context.deleteDatabase("$name.db") }
  }

  @Test fun retentionCannotEraseUnknownOrUnsettledEffects() {
    val context = InstrumentationRegistry.getInstrumentation().targetContext
    val name = "executor-test-${UUID.randomUUID()}"
    try {
      ExecutorStore(context, name).use { store ->
        for (status in listOf("received", "started", "unknown_outcome", "succeeded")) {
          store.put(JSONObject().put("commandId", status).put("commandDigest", "digest-$status")
            .put("status", status).put("receiptSeq", 1))
        }
        store.writableDatabase.execSQL("UPDATE receipts SET updated_ms=1")
        store.put(JSONObject().put("commandId", "new").put("commandDigest", "digest-new")
          .put("status", "received").put("receiptSeq", 1))
        for (status in listOf("received", "started", "unknown_outcome")) assertNotNull(store.get(status))
        assertNull(store.get("succeeded"))
      }
    } finally { context.deleteDatabase("$name.db") }
  }

  @Test fun localStopRemainsEffectiveWhenOutcomePersistenceFails() {
    val instrumentation = InstrumentationRegistry.getInstrumentation()
    val context = instrumentation.targetContext
    val name = "executor-stop-${UUID.randomUUID()}"
    val store = ExecutorStore(context, name)
    val constructor = ExecutorController::class.java.getDeclaredConstructor(Context::class.java).apply { isAccessible = true }
    val controller = constructor.newInstance(context)
    fun field(name: String) = ExecutorController::class.java.getDeclaredField(name).apply { isAccessible = true }
    // Replace only this unarmed test controller's store; no real Phone account is involved.
    (field("store").get(controller) as ExecutorStore).close()
    field("store").set(controller, store)
    val command = JSONObject().put("commandId", "fixture-write").put("commandDigest", "synthetic-digest")
      .put("authorityId", "fixture-A").put("deviceId", "fixture-device").put("bootId", "fixture-boot")
      .put("controlSessionId", "fixture-arm").put("leaseEpoch", 1).put("grantRevision", 1)
    try {
      store.put(JSONObject(command.toString()).put("status", "started").put("receiptSeq", 2))
      store.writableDatabase.execSQL("CREATE TRIGGER simulate_full_disk BEFORE INSERT ON receipts BEGIN SELECT RAISE(FAIL, 'fixture_disk_full'); END")
      instrumentation.runOnMainSync {
        field("binding").set(controller, JSONObject().put("authorityId", "fixture-A").put("deviceId", "fixture-device"))
        field("guard").set(controller, CommandGuard("fixture-A", "fixture-device", "fixture-boot").apply { arm("fixture-arm") })
        field("connected").setBoolean(controller, true)
        field("pending").set(controller, command)
        controller.stop()
        assertFalse(controller.isEnabled())
        assertEquals(false, controller.state()["connected"])
        assertEquals("journal_failed", controller.state()["lastError"])
        assertNull(field("pending").get(controller))
      }
      assertEquals("started", store.get("fixture-write", "fixture-A", "fixture-device")!!.getString("status"))
      store.writableDatabase.execSQL("DROP TRIGGER simulate_full_disk")
      store.recover()
      assertEquals("unknown_outcome", store.get("fixture-write", "fixture-A", "fixture-device")!!.getString("status"))
    } finally { store.close(); context.deleteDatabase("$name.db") }
  }
}
