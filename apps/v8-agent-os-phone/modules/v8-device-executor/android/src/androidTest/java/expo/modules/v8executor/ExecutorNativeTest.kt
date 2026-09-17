package expo.modules.v8executor

import android.content.Context
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.util.UUID

class ExecutorNativeTest {
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
