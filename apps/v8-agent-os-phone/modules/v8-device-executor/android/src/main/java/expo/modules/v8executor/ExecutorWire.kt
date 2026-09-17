package expo.modules.v8executor

import android.util.JsonReader
import android.util.JsonToken
import org.json.JSONArray
import org.json.JSONObject
import java.io.StringReader
import java.security.MessageDigest

object ExecutorWire {
  const val MAX_BYTES = 16_384
  fun revocationConfirmed(status: Int, body: String): Boolean = status in 200..299 ||
    (status == 401 && runCatching { parse(body).optString("code") == "executor_credential_revoked" }.getOrDefault(false))
  fun parse(text: String): JSONObject {
    require(text.toByteArray(Charsets.UTF_8).size <= MAX_BYTES) { "frame_too_large" }
    val reader = JsonReader(StringReader(text)).apply { isLenient = false }
    fun value(depth: Int): Any {
      require(depth <= 12) { "json_too_deep" }
      return when (reader.peek()) {
        JsonToken.BEGIN_OBJECT -> {
          reader.beginObject(); val out = JSONObject(); val names = mutableSetOf<String>()
          while (reader.hasNext()) { val key = reader.nextName(); require(names.add(key)) { "duplicate_key" }; out.put(key, value(depth + 1)) }
          reader.endObject(); out
        }
        JsonToken.BEGIN_ARRAY -> { reader.beginArray(); val out = JSONArray(); while (reader.hasNext()) out.put(value(depth + 1)); reader.endArray(); out }
        JsonToken.STRING -> reader.nextString()
        JsonToken.BOOLEAN -> reader.nextBoolean()
        JsonToken.NULL -> { reader.nextNull(); JSONObject.NULL }
        JsonToken.NUMBER -> {
          val number = reader.nextString()
          require(number.matches(Regex("-?(0|[1-9][0-9]*)"))) { "integer_required" }
          number.toLong()
        }
        else -> error("invalid_json")
      }
    }
    val out = value(0) as? JSONObject ?: error("object_required")
    require(reader.peek() == JsonToken.END_DOCUMENT) { "trailing_json" }
    reader.close(); return out
  }

  private fun quote(value: String): String = buildString {
    append('"')
    value.forEach { c -> when (c) {
      '"' -> append("\\\""); '\\' -> append("\\\\"); '\b' -> append("\\b"); '\u000c' -> append("\\f")
      '\n' -> append("\\n"); '\r' -> append("\\r"); '\t' -> append("\\t")
      else -> if (c.code < 32) append("\\u%04x".format(c.code)) else append(c)
    } }
    append('"')
  }
  fun canonical(value: Any?): String = when (value) {
    null, JSONObject.NULL -> "null"
    is JSONObject -> value.keys().asSequence().toList().sorted().joinToString(",", "{", "}") { quote(it) + ":" + canonical(value.get(it)) }
    is JSONArray -> (0 until value.length()).joinToString(",", "[", "]") { canonical(value.get(it)) }
    is String -> quote(value)
    is Boolean, is Long, is Int -> value.toString()
    else -> error("unsupported_number")
  }
  fun digest(command: JSONObject): String {
    val copy = JSONObject(command.toString()); copy.remove("commandDigest")
    return MessageDigest.getInstance("SHA-256").digest(canonical(copy).toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }
  }
  fun identity(c: JSONObject) = CommandIdentity(c.getString("authorityId"), c.getString("deviceId"), c.getString("bootId"),
    c.getString("controlSessionId"), c.getLong("leaseEpoch"), c.getLong("grantRevision"), c.getLong("capabilityRevision"),
    c.getLong("issuedUnixMs"), c.getLong("deadlineUnixMs"), c.getLong("ttlMs"), c.getString("capability"), c.getString("resourceId"))
}
