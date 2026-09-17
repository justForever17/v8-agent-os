package expo.modules.v8executor

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
}
