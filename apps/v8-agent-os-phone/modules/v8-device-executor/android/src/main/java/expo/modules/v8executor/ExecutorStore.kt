package expo.modules.v8executor

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Only ciphertext leaves Keystore. Configuration/journal never contain the credential. */
class ExecutorStore(context: Context, private val storeName: String = "v8-executor") : SQLiteOpenHelper(context, "$storeName.db", null, 1) {
  private val prefs = context.getSharedPreferences(storeName, Context.MODE_PRIVATE)
  override fun onCreate(db: SQLiteDatabase) {
    db.execSQL("CREATE TABLE receipts (command_id TEXT PRIMARY KEY, digest TEXT NOT NULL, body TEXT NOT NULL, updated_ms INTEGER NOT NULL)")
  }
  override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) = Unit
  fun config(): JSONObject? = prefs.getString("binding", null)?.let(::JSONObject)
  fun saveConfig(config: JSONObject) { check(prefs.edit().putString("binding", config.toString()).commit()) { "storage_failed" } }
  private fun key(): SecretKey {
    val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
    (store.getKey("v8.executor.credential.v1", null) as? SecretKey)?.let { return it }
    return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
      init(KeyGenParameterSpec.Builder("v8.executor.credential.v1", KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
        .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
    }.generateKey()
  }
  fun saveCredential(credential: String) {
    val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, key()) }
    val bytes = cipher.doFinal(credential.toByteArray(Charsets.UTF_8))
    check(prefs.edit().putString("credential", Base64.encodeToString(cipher.iv + bytes, Base64.NO_WRAP)).commit()) { "storage_failed" }
  }
  fun credential(): String {
    val bytes = Base64.decode(prefs.getString("credential", null) ?: error("not_enrolled"), Base64.NO_WRAP)
    val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, bytes.copyOfRange(0, 12))) }
    return cipher.doFinal(bytes.copyOfRange(12, bytes.size)).toString(Charsets.UTF_8)
  }
  fun clearBinding() {
    check(prefs.edit().remove("binding").remove("credential").commit()) { "storage_failed" }
    KeyStore.getInstance("AndroidKeyStore").apply { load(null); deleteEntry("v8.executor.credential.v1") }
  }
  private fun receiptKey(id: String, authority: String, device: String) = "$authority\u001f$device\u001f$id"
  fun get(id: String, authority: String = "", device: String = ""): JSONObject? = readableDatabase.query("receipts", arrayOf("body"), "command_id=?", arrayOf(receiptKey(id, authority, device)), null, null, null).use {
    if (it.moveToFirst()) JSONObject(it.getString(0)) else null
  }
  fun put(receipt: JSONObject) {
    val db = writableDatabase
    val id = receipt.getString("commandId")
    val authority = receipt.optString("authorityId")
    val device = receipt.optString("deviceId")
    if (get(id, authority, device) == null) {
      // Unsettled and unknown outcomes remain available for reconciliation.
      // Expired settled records cannot execute again because admission enforces 30s TTL.
      val expired = mutableListOf<String>()
      db.query("receipts", arrayOf("command_id", "body"), "updated_ms < ?",
        arrayOf((System.currentTimeMillis() - 7 * 86_400_000L).toString()), null, null, null).use { cursor ->
        while (cursor.moveToNext()) if (JSONObject(cursor.getString(1)).getString("status") in
          setOf("succeeded", "failed", "rejected", "cancelled", "expired")) expired.add(cursor.getString(0))
      }
      expired.forEach { db.delete("receipts", "command_id=?", arrayOf(it)) }
      db.rawQuery("SELECT count(*) FROM receipts", null).use { it.moveToFirst(); require(it.getInt(0) < 512) { "journal_full" } }
    }
    val values = ContentValues().apply {
      put("command_id", receiptKey(id, authority, device)); put("digest", receipt.getString("commandDigest"))
      put("body", receipt.toString()); put("updated_ms", System.currentTimeMillis())
    }
    check(db.insertWithOnConflict("receipts", null, values, SQLiteDatabase.CONFLICT_REPLACE) != -1L) { "journal_failed" }
  }
  fun recover(): List<JSONObject> = readableDatabase.query("receipts", arrayOf("body"), null, null, null, null, "updated_ms DESC").use { cursor ->
    val out = mutableListOf<JSONObject>()
    while (cursor.moveToNext()) {
      val receipt = JSONObject(cursor.getString(0))
      if (receipt.getString("status") in setOf("received", "started")) {
        receipt.put("status", if (receipt.getString("status") == "started") "unknown_outcome" else "cancelled")
          .put("error", "process_restarted").put("receiptSeq", receipt.getLong("receiptSeq") + 1)
        out.add(receipt)
      }
    }
    out
  }.also { it.forEach(::put) }
  fun recent(): JSONArray = JSONArray().also { out ->
    readableDatabase.query("receipts", arrayOf("body"), null, null, null, null, "updated_ms DESC", "10").use { cursor ->
      while (cursor.moveToNext()) {
        val r = JSONObject(cursor.getString(0)); out.put(JSONObject().put("commandId", r.getString("commandId"))
          .put("status", r.getString("status")).put("error", r.optString("error")))
      }
    }
  }
}
