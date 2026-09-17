package expo.modules.v8executor

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.net.URI
import java.util.UUID
import java.util.concurrent.FutureTask
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

class ExecutorController private constructor(private val context: Context) {
  private val main = Handler(Looper.getMainLooper())
  private val store = ExecutorStore(context)
  private val client = OkHttpClient.Builder().followRedirects(false).followSslRedirects(false)
    .callTimeout(15, TimeUnit.SECONDS).connectTimeout(10, TimeUnit.SECONDS).readTimeout(20, TimeUnit.SECONDS).build()
  val bootId = UUID.randomUUID().toString()
  private var binding = store.config()
  private var guard: CommandGuard? = createGuard()
  private var socket: WebSocket? = null
  private var generation = 0
  private var connected = false
  private var pending: JSONObject? = null
  private var pendingWork: CommandWork? = null
  private var deadline: Runnable? = null
  private var applying = false
  private var lastError = if (binding == null) "not_enrolled" else if (binding?.optBoolean("pendingRevocation") == true) "revocation_pending" else "requires_local_resume"
  private var backoffMs = 1_000L
  private val queuedFrames = AtomicInteger(0)
  var onState: ((Map<String, Any?>) -> Unit)? = null
  private val heartbeat = object : Runnable {
    override fun run() {
      if (!isEnabled()) return
      if (!notificationsGranted()) { stop("requires_notification_permission"); return }
      socket?.send("{\"type\":\"ping\"}")
      val active = pending
      if (active != null && guard?.check(ExecutorWire.identity(active), SystemClock.elapsedRealtime(), System.currentTimeMillis()) != null) settlePending("lease_or_deadline_expired")
      main.postDelayed(this, 10_000)
    }
  }
  init { store.recover() }
  private fun createGuard(): CommandGuard? = binding?.let {
    CommandGuard(it.getString("authorityId"), it.getString("deviceId"), bootId, it.optLong("leaseEpoch"), it.optLong("grantRevision"))
      .apply { localApps = apps(it.getJSONArray("allowedApps")) }
  }
  private fun apps(array: JSONArray): Set<String> = (0 until array.length()).map { array.getString(it) }.toSet()
  fun isEnabled() = guard?.enabled == true
  fun isApplying() = applying
  fun allowedApps() = guard?.localApps ?: emptySet()
  fun <T> onMain(block: () -> T): T {
    if (Looper.myLooper() == Looper.getMainLooper()) return block()
    val task = FutureTask(block); main.post(task); return task.get(20, TimeUnit.SECONDS)
  }
  fun state(): Map<String, Any?> = mapOf(
    "supported" to true, "enabled" to isEnabled(), "connected" to connected,
    "accessibilityGranted" to (ExecutorAccessibilityService.instance != null),
    "notificationGranted" to notificationsGranted(), "authorityId" to binding?.optString("authorityId"),
    "androidApi" to Build.VERSION.SDK_INT, "fullDisplayCapture" to fullDisplayCapture(),
    "windowCaptureAvailable" to (Build.VERSION.SDK_INT >= 34), "gestureAvailable" to (Build.VERSION.SDK_INT >= 34),
    "gestureUnavailableReason" to (if (Build.VERSION.SDK_INT >= 34) "" else "requires_android_14_window_capture"),
    "profileAuthorityKey" to binding?.optString("profileAuthorityKey"), "deviceId" to binding?.optString("deviceId"),
    "baseUrl" to binding?.optString("baseUrl"), "name" to binding?.optString("name"),
    "allowedApps" to allowedApps().toList(), "grantRevision" to maxOf(guard?.grantRevision ?: 0, binding?.optLong("grantRevision") ?: 0),
    "status" to (if (!isEnabled()) "stopped" else if (!connected) "connecting" else if (pending != null) "executing" else "ready"),
    "lastError" to lastError, "recentReceipts" to (0 until store.recent().length()).map { index ->
      val receipt = store.recent().getJSONObject(index)
      mapOf("commandId" to receipt.getString("commandId"), "status" to receipt.getString("status"), "error" to receipt.optString("error"))
    }
  )
  private fun publish() { runCatching { onState?.invoke(state()) } }
  fun observationIdentity() = JSONObject().put("deviceId", binding?.getString("deviceId"))
    .put("bootId", bootId).put("controlSessionId", guard?.controlSessionId)
  fun permissionChanged(granted: Boolean) { if (!granted) stop("accessibility_revoked") else publish() }
  fun observationChanged(windowChanged: Boolean) {
    val command = pending ?: return
    if (command.getString("capability") == "android.capture") finish(command, "failed", "window_changed")
    else if (command.getJSONObject("arguments").optString("action") in setOf("tap", "swipe")) {
      if (!applying) finish(command, "rejected", "stale_frame")
      else if (windowChanged) settlePending("window_changed_during_gesture")
    }
  }
  fun fullDisplayCapture() = binding?.optBoolean("fullDisplayCapture", false) == true
  private fun notificationsGranted() = (Build.VERSION.SDK_INT < 33 || context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED) &&
    context.getSystemService(NotificationManager::class.java).areNotificationsEnabled()

  fun enroll(ticket: String, authorityId: String, baseUrl: String, name: String, profileAuthorityKey: String, allowedApps: List<String>) {
    require(ticket.length in 16..4096 && authorityId.isNotBlank() && profileAuthorityKey.isNotBlank()) { "invalid_enrollment" }
    require(onMain { binding == null }) { "revoke_previous_binding_first" }
    val origin = URI(baseUrl)
    require(origin.scheme == "https" && origin.host != null && origin.rawUserInfo == null && origin.rawQuery == null && origin.rawFragment == null && origin.path in listOf("", "/")) { "https_origin_required" }
    validateApps(allowedApps)
    val base = baseUrl.trimEnd('/')
    val payload = JSONObject().put("ticket", ticket).put("authorityId", authorityId).put("name", name.take(80)).put("deviceClass", "android")
    val request = Request.Builder().url("$base/api/executor/enroll").post(payload.toString().toRequestBody("application/json".toMediaType())).build()
    val response = client.newCall(request).execute().use {
      require(it.isSuccessful) { "enrollment_failed_${it.code}" }
      val body = it.body ?: error("empty_enrollment")
      require(body.contentLength() <= ExecutorWire.MAX_BYTES) { "enrollment_too_large" }
      val source = body.source(); source.request((ExecutorWire.MAX_BYTES + 1).toLong())
      require(source.buffer.size <= ExecutorWire.MAX_BYTES) { "enrollment_too_large" }
      ExecutorWire.parse(source.readUtf8())
    }
    require(response.getString("authorityId") == authorityId) { "authority_mismatch" }
    onMain {
      require(binding == null) { "binding_changed" }
      store.saveCredential(response.getString("credential"))
      val config = JSONObject().put("authorityId", authorityId).put("deviceId", response.getString("deviceId"))
        .put("baseUrl", base).put("name", name.take(80)).put("profileAuthorityKey", profileAuthorityKey)
        .put("grantRevision", response.getLong("grantRevision")).put("allowedApps", JSONArray(allowedApps)).put("leaseEpoch", 0)
      store.saveConfig(config); binding = config; guard = createGuard(); lastError = "requires_local_resume"; publish()
    }
  }
  private fun validateApps(allowedApps: List<String>) {
    require(allowedApps.size in 1..16 && allowedApps.distinct().size == allowedApps.size) { "invalid_apps" }
    require(allowedApps.all { it.matches(Regex("[A-Za-z][A-Za-z0-9_]*(\\.[A-Za-z0-9_]+)+")) && it != context.packageName && it != "com.android.settings" && !it.contains("permissioncontroller") }) { "protected_or_invalid_app" }
  }
  fun setAllowedApps(allowedApps: List<String>) {
    validateApps(allowedApps); stop("scope_changed")
    val config = binding ?: error("not_enrolled"); config.put("allowedApps", JSONArray(allowedApps)); store.saveConfig(config)
    guard?.localApps = allowedApps.toSet(); publish()
  }
  fun setFullDisplayCapture(enabled: Boolean) {
    stop("scope_changed")
    val config = binding ?: error("not_enrolled")
    config.put("fullDisplayCapture", enabled); store.saveConfig(config); publish()
  }
  fun acknowledgeGrantRevision(revision: Long) {
    val config = binding ?: error("not_enrolled")
    require(revision >= config.optLong("grantRevision")) { "stale_revision" }
    config.put("grantRevision", revision); store.saveConfig(config); publish()
  }
  fun enable() {
    require(ExecutorAccessibilityService.instance != null) { "requires_accessibility_permission" }
    require(notificationsGranted()) { "requires_notification_permission" }
    require(binding?.optBoolean("pendingRevocation") != true) { "revocation_pending" }
    val current = guard ?: error("not_enrolled")
    stop("rearming"); current.arm(UUID.randomUUID().toString()); lastError = ""
    try { showNotification(); connect(); main.postDelayed(heartbeat, 10_000); publish() }
    catch (_: Exception) { stop("credential_or_connection_unavailable"); error("credential_or_connection_unavailable") }
  }
  fun stop(reason: String = "local_stop") {
    pendingWork?.cancel()
    guard?.stop(); connected = false; lastError = reason; generation++
    main.removeCallbacks(heartbeat)
    try { settlePending(reason) }
    catch (_: Exception) { pending = null; applying = false; lastError = "journal_failed" }
    finally {
      ExecutorAccessibilityService.instance?.clearObservation()
      socket?.close(1000, "local_stop"); socket = null
      context.getSystemService(NotificationManager::class.java).cancel(NOTIFICATION_ID)
    }
    publish()
  }
  fun forgetProfile(profileAuthorityKey: String): Boolean {
    if (binding?.optString("profileAuthorityKey") != profileAuthorityKey) return false
    stop("bound_profile_forgotten"); return true
  }
  fun revoke(): Boolean {
    val config = onMain {
      stop("revoking")
      binding?.let { it.put("pendingRevocation", true); store.saveConfig(it); JSONObject(it.toString()) }
    } ?: return true
    val request = Request.Builder().url(config.getString("baseUrl") + "/api/executor/revoke")
      .header("Authorization", "Bearer " + store.credential()).post("{}".toRequestBody("application/json".toMediaType())).build()
    val revoked = try { client.newCall(request).execute().use { it.isSuccessful } } catch (_: Exception) { false }
    onMain {
      if (revoked && binding?.optString("deviceId") == config.getString("deviceId")) {
        store.clearBinding(); binding = null; guard = null; lastError = "not_enrolled"
      } else lastError = "revocation_pending"
      publish()
    }
    return revoked
  }
  private fun showNotification() {
    val manager = context.getSystemService(NotificationManager::class.java)
    manager.createNotificationChannel(NotificationChannel("v8-executor", context.getString(R.string.v8_executor_name), NotificationManager.IMPORTANCE_LOW))
    val stop = PendingIntent.getBroadcast(context, 0, Intent(context, ExecutorStopReceiver::class.java), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    val open = Intent(Intent.ACTION_VIEW, android.net.Uri.parse("v8agentosphone://device-executor")).setPackage(context.packageName)
    val content = PendingIntent.getActivity(context, 1, open, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    manager.notify(NOTIFICATION_ID, Notification.Builder(context, "v8-executor").setSmallIcon(android.R.drawable.ic_menu_view)
      .setContentTitle(context.getString(R.string.v8_executor_active)).setContentText(binding?.optString("baseUrl"))
      .setContentIntent(content).setOngoing(true).addAction(Notification.Action.Builder(null, context.getString(R.string.v8_executor_stop), stop).build()).build())
  }
  private fun capabilities(): JSONArray = JSONArray().also { out ->
    allowedApps().sorted().forEach { resource ->
      val supported = mutableListOf("android.observe", "android.action")
      if (Build.VERSION.SDK_INT >= 34 || fullDisplayCapture()) supported.add("android.capture")
      supported.forEach { out.put(JSONObject().put("capability", it).put("resourceId", resource)) }
    }
    if (fullDisplayCapture()) out.put(JSONObject().put("capability", "android.capture").put("resourceId", "display"))
  }
  private fun connect() {
    if (!isEnabled() || ExecutorAccessibilityService.instance == null) return
    val config = binding ?: return
    val currentGeneration = ++generation
    val request = Request.Builder().url(config.getString("baseUrl").replaceFirst("https://", "wss://") + "/api/executor/ws")
      .header("Authorization", "Bearer " + store.credential()).build()
    socket = client.newWebSocket(request, object : WebSocketListener() {
      override fun onOpen(webSocket: WebSocket, response: Response) { main.post {
        if (currentGeneration != generation || !isEnabled()) { webSocket.close(1000, "stale_connection"); return@post }
        webSocket.send(JSONObject().put("type", "hello").put("protocolVersion", 1).put("deviceId", config.getString("deviceId"))
          .put("authorityId", config.getString("authorityId")).put("bootId", bootId).put("controlSessionId", guard?.controlSessionId)
          .put("capabilityRevision", 1).put("capabilities", capabilities()).put("localEnabled", true).toString())
      } }
      override fun onMessage(webSocket: WebSocket, text: String) {
        if (text.toByteArray(Charsets.UTF_8).size > ExecutorWire.MAX_BYTES) { webSocket.close(1009, "frame_limit"); return }
        if (queuedFrames.incrementAndGet() > 8) { queuedFrames.decrementAndGet(); webSocket.close(1009, "frame_limit"); return }
        main.post {
          queuedFrames.decrementAndGet()
          if (currentGeneration != generation) return@post
          try { handle(ExecutorWire.parse(text)) } catch (_: Exception) { lastError = "invalid_server_frame"; webSocket.close(1008, "invalid_frame"); publish() }
        }
      }
      override fun onMessage(webSocket: WebSocket, bytes: okio.ByteString) { webSocket.close(1003, "text_only") }
      override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) { main.post {
        if (currentGeneration == generation && response?.code in listOf(401, 403)) stop("remote_authorization_revoked")
        else disconnected(currentGeneration)
      } }
      override fun onClosed(webSocket: WebSocket, code: Int, reason: String) { main.post { disconnected(currentGeneration) } }
      override fun onClosing(webSocket: WebSocket, code: Int, reason: String) { webSocket.close(code, null) }
    })
  }
  private fun disconnected(connectionGeneration: Int) {
    if (connectionGeneration != generation) return
    connected = false; guard?.disconnect()
    ExecutorAccessibilityService.instance?.clearObservation()
    try { settlePending("connection_lost") }
    catch (_: Exception) { stop("journal_failed"); return }
    socket = null; lastError = "offline"; publish()
    if (isEnabled()) {
      val retryGeneration = generation; val delay = backoffMs; backoffMs = minOf(30_000, backoffMs * 2)
      main.postDelayed({ if (retryGeneration == generation && isEnabled()) connect() }, delay)
    }
  }
  private fun handle(frame: JSONObject) {
    when (frame.getString("type")) {
      "session", "lease" -> {
        val config = binding ?: error("not_enrolled")
        for ((key, value) in mapOf("authorityId" to config.getString("authorityId"), "deviceId" to config.getString("deviceId"), "bootId" to bootId, "controlSessionId" to guard!!.controlSessionId)) {
          require(frame.getString(key) == value) { "session_identity_mismatch" }
        }
        require(frame.getInt("protocolVersion") == 1) { "unsupported_protocol" }
        val array = frame.getJSONArray("grants")
        val grants = (0 until array.length()).map { array.getJSONObject(it).let { grant -> grant.getString("capability") to grant.getString("resourceId") } }.toSet()
        if (connected && frame.getLong("grantRevision") != guard!!.grantRevision) { stop("grant_or_lease_changed"); return }
        guard!!.lease(frame.getLong("leaseEpoch"), frame.getLong("grantRevision"), frame.getLong("serverUnixMs"), frame.getLong("leaseExpiresUnixMs"), SystemClock.elapsedRealtime(), grants)
        config.put("leaseEpoch", guard!!.leaseEpoch).put("grantRevision", guard!!.grantRevision); store.saveConfig(config)
        pending?.let { if (checkCommand(it) != null) settlePending("grant_or_lease_changed") }
        connected = true; backoffMs = 1_000; lastError = ""; publish()
      }
      "command" -> command(frame)
      "receipt_ack" -> Unit // An acknowledgement does not change the persisted device outcome.
      "query" -> history(frame.getString("commandId"))?.let(::sendReceipt)
      "cancel" -> {
        val c = pending
        if (c != null && c.getString("commandId") == frame.getString("commandId") && c.getString("commandDigest") == frame.getString("commandDigest")) settlePending("cancel_requested")
        else history(frame.getString("commandId"))?.let(::sendReceipt)
      }
      "stop" -> stop("remote_stop")
      else -> error("unsupported_frame")
    }
  }
  private fun command(c: JSONObject) {
    if (!notificationsGranted()) { stop("requires_notification_permission"); return }
    require(connected && c.getInt("protocolVersion") == 1 && c.getString("commandId").length in 1..128) { "invalid_command" }
    require(c.getString("authorityId") == binding?.getString("authorityId") && c.getString("deviceId") == binding?.getString("deviceId")) { "wrong_device" }
    require(ExecutorWire.digest(c) == c.getString("commandDigest")) { "digest_mismatch" }
    val previous = history(c.getString("commandId"))
    if (previous != null) {
      require(previous.getString("commandDigest") == c.getString("commandDigest")) { "command_conflict" }
      sendReceipt(previous); return
    }
    val identity = ExecutorWire.identity(c)
    val error = checkCommand(c)
    if (error != null) { record(c, "rejected", error); return }
    if (pending != null) { record(c, "rejected", "device_busy"); return }
    if (identity.capability !in listOf("android.observe", "android.action", "android.capture")) { record(c, "rejected", "unsupported_capability"); return }
    record(c, "received"); pending = c; pendingWork = CommandWork(); publish()
    deadline = Runnable { if (pending === c) settlePending("command_deadline_expired") }.also {
      main.postDelayed(it, guard!!.remaining(identity, SystemClock.elapsedRealtime(), System.currentTimeMillis()).coerceAtLeast(1))
    }
    main.post {
      if (pending === c) try { apply(c) }
      catch (_: Exception) { stop("native_action_error") }
    }
  }
  private fun apply(c: JSONObject) {
    if (!notificationsGranted()) { stop("requires_notification_permission"); return }
    val identity = ExecutorWire.identity(c)
    checkCommand(c)?.let { finish(c, "rejected", it); return }
    val work = pendingWork ?: return
    val driver = ExecutorAccessibilityService.instance
    if (driver == null) { settlePending("accessibility_revoked"); return }
    if (identity.capability == "android.capture") { applyCapture(c, work, driver); return }
    val gesture = identity.capability == "android.action" && c.getJSONObject("arguments").optString("action") in setOf("tap", "swipe")
    if (gesture) {
      try {
        driver.gesture(identity.resourceId, c.getJSONObject("arguments"), c.getJSONObject("precondition"), work, { current(c, work) }, {
          require(current(c, work)) { "gesture_cancelled" }
          val duration = c.getJSONObject("arguments").optLong("durationMs", 60)
          require(guard!!.remaining(identity, SystemClock.elapsedRealtime(), System.currentTimeMillis()) > duration) { "insufficient_gesture_deadline" }
          record(c, "started"); applying = true
        }) { status, error ->
          if (current(c, work)) {
            if (status == "succeeded") postAction(c, work, driver, true)
            else finish(c, status, error?.takeIf { it.matches(Regex("[a-z_]{1,80}")) } ?: "gesture_failed")
          }
        }
      } catch (error: Exception) { finish(c, "rejected", safeError(error)) }
      return
    }
    try {
      if (identity.capability == "android.action") driver.validateAction(identity.resourceId, c.getJSONObject("arguments"), c.getJSONObject("precondition"))
    } catch (error: Exception) { finish(c, "rejected", safeError(error)); return }
    record(c, "started"); applying = true
    try {
      if (identity.capability == "android.observe") {
        finish(c, "succeeded", observation = driver.observe(identity.resourceId))
      } else {
        val accepted = driver.applyAction(identity.resourceId, c.getJSONObject("arguments"), c.getJSONObject("precondition"))
        postAction(c, work, driver, accepted)
      }
    } catch (error: Exception) { finish(c, "unknown_outcome", safeError(error)) }
  }
  private fun checkCommand(c: JSONObject): String? {
    val identity = ExecutorWire.identity(c)
    val currentGuard = guard ?: return "locally_stopped"
    currentGuard.check(identity, SystemClock.elapsedRealtime(), System.currentTimeMillis())?.let { return it }
    return try {
      if (identity.capability == "android.capture") {
        val arguments = c.getJSONObject("arguments")
        require(arguments.keys().asSequence().all { it == "scope" }) { "invalid_capture_arguments" }
        CapturePolicy.authorize(Build.VERSION.SDK_INT, arguments.optString("scope", "window"), fullDisplayCapture(), currentGuard.hasGrant("android.capture", "display"))
      }
      if (identity.capability == "android.action" && c.getJSONObject("arguments").optString("action") in setOf("tap", "swipe")) {
        require(Build.VERSION.SDK_INT >= 34) { "requires_android_14_window_capture" }
        require(currentGuard.hasGrant("android.capture", identity.resourceId)) { "capture_grant_required" }
      }
      null
    } catch (error: Exception) { safeError(error) }
  }
  private fun current(c: JSONObject, work: CommandWork): Boolean {
    if (pending !== c || pendingWork !== work || !work.active) return false
    return try {
      if (!notificationsGranted() || ExecutorAccessibilityService.instance == null || checkCommand(c) != null) {
        settlePending("permission_or_deadline_changed"); false
      } else true
    } catch (_: Exception) { stop("journal_failed"); false }
  }
  private fun applyCapture(c: JSONObject, work: CommandWork, driver: ExecutorAccessibilityService) {
    record(c, "started")
    try {
      driver.capture(c.getString("resourceId"), c.getJSONObject("arguments").optString("scope", "window"), work, { current(c, work) }) { result ->
        if (!current(c, work)) { result.getOrNull()?.jpeg?.fill(0); return@capture }
        val image = result.getOrNull()
        if (image == null) { finish(c, "failed", safeError(result.exceptionOrNull() as? Exception ?: IllegalStateException("capture_unavailable"))); return@capture }
        val config = binding ?: return@capture
        fun validImage(): Boolean {
          if (!current(c, work)) return false
          return try { driver.validateCaptureTarget(image.target); true }
          catch (_: Exception) { finish(c, "failed", "window_changed"); false }
        }
        try {
          ExecutorMediaUploader(client, config.getString("baseUrl"), store.credential()).upload(c, image, work, ::validImage) { uploaded ->
            if (!validImage()) return@upload
            try {
              if (uploaded.isSuccess) {
                driver.acceptCapturedFrame(uploaded.getOrThrow())
                finish(c, "succeeded", observation = uploaded.getOrThrow().observation)
              } else finish(c, "failed", safeError(uploaded.exceptionOrNull() as? Exception ?: IllegalStateException("media_upload_failed")))
            } catch (error: Exception) { finish(c, "failed", safeError(error)) }
          }
        } catch (error: Exception) { image.jpeg.fill(0); finish(c, "failed", safeError(error)) }
      }
    } catch (error: Exception) { finish(c, "failed", safeError(error)) }
  }
  private fun postAction(c: JSONObject, work: CommandWork, driver: ExecutorAccessibilityService, accepted: Boolean) {
    val delay = minOf(250L, guard!!.remaining(ExecutorWire.identity(c), SystemClock.elapsedRealtime(), System.currentTimeMillis()).coerceAtLeast(0))
    main.postDelayed({
      if (!current(c, work)) return@postDelayed
      val observation = try {
        if (guard?.hasGrant("android.observe", c.getString("resourceId")) == true) driver.observe(c.getString("resourceId")) else null
      } catch (_: Exception) { null }
      try { finish(c, if (accepted) "succeeded" else "failed", if (observation == null) "post_observation_unavailable" else null, observation, accepted) }
      catch (_: Exception) { stop("journal_failed") }
    }, delay)
  }
  private fun finish(c: JSONObject, status: String, error: String? = null, observation: JSONObject? = null, driverAccepted: Boolean? = null) {
    if (pending !== c) return
    try { record(c, status, error, observation, driverAccepted) }
    catch (_: Exception) { stop("journal_failed"); return }
    if (status == "succeeded") pendingWork?.complete() else pendingWork?.cancel()
    pendingWork = null; pending = null; applying = false
    deadline?.let(main::removeCallbacks); deadline = null; publish()
  }
  private fun settlePending(reason: String) {
    pendingWork?.cancel(); pendingWork = null
    deadline?.let(main::removeCallbacks); deadline = null
    val interrupted = pending
    pending = null; applying = false
    try {
      interrupted?.let { c -> record(c, if (history(c.getString("commandId"))?.optString("status") == "started") "unknown_outcome" else "cancelled", reason) }
    } catch (_: Exception) { stop("journal_failed") }
  }
  private fun safeError(error: Exception) = error.message?.takeIf { it.matches(Regex("[a-z_]{1,80}")) } ?: "native_action_error"
  private fun record(c: JSONObject, status: String, error: String? = null, observation: JSONObject? = null, driverAccepted: Boolean? = null) {
    val previous = history(c.getString("commandId"))
    val receipt = JSONObject().put("type", "receipt").put("protocolVersion", 1).put("status", status)
      .put("receiptSeq", (previous?.optLong("receiptSeq") ?: 0) + 1).put("deviceMonotonicMs", SystemClock.elapsedRealtime())
    for (key in listOf("commandId", "commandDigest", "authorityId", "deviceId", "bootId", "controlSessionId", "leaseEpoch", "grantRevision")) receipt.put(key, c.get(key))
    if (error != null) receipt.put("error", error)
    if (observation != null) receipt.put("observation", observation)
    if (driverAccepted != null) receipt.put("driverAccepted", driverAccepted)
    receipt.put("businessVerification", "unverified")
    require(receipt.toString().toByteArray(Charsets.UTF_8).size <= ExecutorWire.MAX_BYTES) { "receipt_too_large" }
    store.put(receipt); sendReceipt(receipt)
  }
  private fun history(id: String) = binding?.let { store.get(id, it.getString("authorityId"), it.getString("deviceId")) }
  private fun sendReceipt(receipt: JSONObject) {
    if (receipt.optString("authorityId") == binding?.optString("authorityId") && receipt.optString("deviceId") == binding?.optString("deviceId")) socket?.send(receipt.toString())
  }

  companion object {
    private const val NOTIFICATION_ID = 8170
    @Volatile private var instance: ExecutorController? = null
    fun get(context: Context): ExecutorController = instance ?: synchronized(this) {
      instance ?: ExecutorController(context.applicationContext).also { instance = it }
    }
  }
}

class ExecutorStopReceiver : BroadcastReceiver() {
  override fun onReceive(context: Context, intent: Intent) { ExecutorController.get(context).stop("local_stop") }
}
