package expo.modules.v8executor

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.app.KeyguardManager
import android.content.Context
import android.content.BroadcastReceiver
import android.content.Intent
import android.content.IntentFilter
import android.os.Build
import android.graphics.Rect
import android.graphics.Path
import android.graphics.Point
import android.hardware.display.DisplayManager
import android.os.Bundle
import android.view.KeyEvent
import android.view.Display
import android.view.WindowInsets
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

class ExecutorAccessibilityService : AccessibilityService() {
  private data class Node(val info: AccessibilityNodeInfo, val fingerprint: String)
  private val nodes = mutableMapOf<String, Node>()
  private var observation: JSONObject? = null
  private var revision = 0L
  private var observationMono = 0L
  private val capture by lazy { ExecutorCapture(this) }
  private var capturedFrame: JSONObject? = null
  private var capturedTarget: CaptureTarget? = null
  private var capturedMono = 0L
  private val controller get() = ExecutorController.get(this)
  private var receiverRegistered = false
  private val screenOff = object : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) { controller.stop("device_locked") }
  }

  override fun onServiceConnected() {
    instance = this
    if (!receiverRegistered) {
      if (Build.VERSION.SDK_INT >= 33) registerReceiver(screenOff, IntentFilter(Intent.ACTION_SCREEN_OFF), Context.RECEIVER_NOT_EXPORTED)
      else registerReceiver(screenOff, IntentFilter(Intent.ACTION_SCREEN_OFF))
      receiverRegistered = true
    }
    controller.permissionChanged(true)
  }
  override fun onInterrupt() { controller.stop("service_interrupted") }
  override fun onUnbind(intent: Intent): Boolean { instance = null; controller.permissionChanged(false); return super.onUnbind(intent) }
  override fun onDestroy() {
    if (receiverRegistered) { unregisterReceiver(screenOff); receiverRegistered = false }
    clearObservation(); capture.close(); instance = null; controller.permissionChanged(false); super.onDestroy()
  }
  override fun onKeyEvent(event: KeyEvent): Boolean {
    if (event.action == KeyEvent.ACTION_DOWN) controller.stop("local_key_interaction")
    return false
  }
  override fun onAccessibilityEvent(event: AccessibilityEvent) {
    revision++
    if (!controller.isEnabled()) return
    controller.observationChanged(event.eventType in setOf(AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED, AccessibilityEvent.TYPE_WINDOWS_CHANGED))
    if (getSystemService(KeyguardManager::class.java).isKeyguardLocked) { controller.stop("device_locked"); return }
    if (event.eventType == AccessibilityEvent.TYPE_TOUCH_INTERACTION_START || event.eventType == AccessibilityEvent.TYPE_GESTURE_DETECTION_START) {
      controller.stop("local_touch_interaction")
    } else if (!controller.isApplying() && event.eventType in setOf(AccessibilityEvent.TYPE_VIEW_CLICKED,
        AccessibilityEvent.TYPE_VIEW_LONG_CLICKED, AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED, AccessibilityEvent.TYPE_VIEW_SCROLLED)) {
      controller.stop("local_ui_interaction")
    }
  }

  fun clearObservation() {
    nodes.values.forEach { it.info.recycle() }; nodes.clear(); observation = null
    capturedFrame = null; capturedTarget = null
  }
  private fun protectedPackage(app: String) = app == packageName || app == "android" || app == "com.android.settings" ||
    app == "com.android.systemui" || app.contains("permissioncontroller") || app.contains("packageinstaller") ||
    app == "com.miui.securitycenter"
  private fun allowedRoot(resource: String): AccessibilityNodeInfo {
    require(!getSystemService(KeyguardManager::class.java).isKeyguardLocked) { "device_locked" }
    require(resource in controller.allowedApps()) { "app_not_allowed" }
    require(!protectedPackage(resource)) { "protected_ui" }
    val root = rootInActiveWindow ?: error("window_unavailable")
    if (root.packageName?.toString() != resource) { root.recycle(); error("window_changed") }
    if (!root.refresh()) { root.recycle(); error("window_unavailable") }
    return root
  }
  private fun fingerprint(node: AccessibilityNodeInfo): String {
    val rect = Rect(); node.getBoundsInScreen(rect)
    return listOf(node.windowId, node.packageName, node.viewIdResourceName, node.className, node.text,
      node.contentDescription, rect.toShortString(), node.isEnabled, node.isVisibleToUser, node.isPassword,
      node.isClickable, node.isEditable, node.childCount).joinToString("|")
  }
  fun observe(resource: String): JSONObject {
    clearObservation()
    val root = allowedRoot(resource)
    val window = root.windowId
    val pending = ArrayDeque<AccessibilityNodeInfo>(); pending.add(root)
    val out = JSONArray(); var partial = false; var visited = 0
    while (pending.isNotEmpty() && visited < 100 && out.length() < 20) {
      val node = pending.removeFirst(); visited++
      if (node.packageName?.toString() == resource && !node.isPassword && node.isVisibleToUser) {
        val nodeId = "n${out.length()}"
        nodes[nodeId] = Node(node, fingerprint(node))
        val rect = Rect(); node.getBoundsInScreen(rect)
        val supported = JSONArray()
        val actions = mapOf(AccessibilityNodeInfo.ACTION_CLICK to "click", AccessibilityNodeInfo.ACTION_LONG_CLICK to "long_click",
          AccessibilityNodeInfo.ACTION_SET_TEXT to "set_text", AccessibilityNodeInfo.ACTION_SCROLL_FORWARD to "scroll_forward",
          AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD to "scroll_backward")
        node.actionList.forEach { action -> actions[action.id]?.let(supported::put) }
        out.put(JSONObject().put("nodeId", nodeId).put("text", node.text?.toString()?.take(100) ?: "")
          .put("description", node.contentDescription?.toString()?.take(100) ?: "").put("className", node.className?.toString()?.take(100) ?: "")
          .put("enabled", node.isEnabled).put("actions", supported)
          .put("bounds", JSONArray(listOf(rect.left, rect.top, rect.right, rect.bottom))))
      }
      if (!node.isPassword) for (index in 0 until minOf(node.childCount, 100)) node.getChild(index)?.let(pending::add)
      if (node.childCount > 100 || (node.text?.length ?: 0) > 100 || (node.contentDescription?.length ?: 0) > 100) partial = true
      if (nodes.values.none { it.info === node }) node.recycle()
    }
    if (pending.isNotEmpty()) partial = true
    pending.forEach { it.recycle() }
    observationMono = android.os.SystemClock.elapsedRealtime()
    return controller.observationIdentity().put("observationId", UUID.randomUUID().toString())
      .put("resourceId", resource).put("appId", resource).put("windowId", window.toString())
      .put("nodeMapRevision", revision.toString()).put("observedUnixMs", System.currentTimeMillis())
      .put("partial", partial).put("nodes", out).put("availability", "tree_only")
      .also { observation = it }
  }
  fun validateAction(resource: String, arguments: JSONObject, precondition: JSONObject): AccessibilityNodeInfo {
    val previous = observation ?: error("observation_required")
    require(android.os.SystemClock.elapsedRealtime() - observationMono <= 10_000) { "observation_expired" }
    for (key in listOf("observationId", "deviceId", "bootId", "controlSessionId", "resourceId", "appId", "windowId", "nodeMapRevision")) {
      require(previous.getString(key) == precondition.getString(key)) { "stale_observation" }
    }
    require(previous.getString("nodeMapRevision") == revision.toString() && previous.getString("resourceId") == resource) { "stale_observation" }
    val root = allowedRoot(resource)
    try { require(root.windowId.toString() == previous.getString("windowId")) { "window_changed" } } finally { root.recycle() }
    val node = nodes[arguments.getString("nodeId")] ?: error("node_unavailable")
    require(node.info.refresh() && fingerprint(node.info) == node.fingerprint) { "node_changed" }
    require(!node.info.isPassword && node.info.isEnabled && node.info.isVisibleToUser) { "node_unavailable" }
    return node.info
  }
  fun applyAction(resource: String, arguments: JSONObject, precondition: JSONObject): Boolean {
    val node = validateAction(resource, arguments, precondition)
    val action = when (arguments.getString("action")) {
      "click" -> AccessibilityNodeInfo.ACTION_CLICK
      "long_click" -> AccessibilityNodeInfo.ACTION_LONG_CLICK
      "scroll_forward" -> AccessibilityNodeInfo.ACTION_SCROLL_FORWARD
      "scroll_backward" -> AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD
      "set_text" -> AccessibilityNodeInfo.ACTION_SET_TEXT
      else -> error("unsupported_action")
    }
    require(node.actionList.any { it.id == action }) { "action_unavailable" }
    val extras = if (action == AccessibilityNodeInfo.ACTION_SET_TEXT) Bundle().apply {
      val text = arguments.getString("text"); require(text.length <= 1000) { "text_too_long" }
      putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
    } else null
    return node.performAction(action, extras)
  }

  data class CaptureTarget(val resource: String, val windowId: Int, val displayId: Int, val rotation: Int,
    val viewport: Viewport, val interactionBounds: Viewport, val scope: String, val revision: Long, val windows: String,
    val identity: String, val observationId: String, val observedUnixMs: Long, val observedMono: Long)

  fun captureTarget(resource: String, scope: String): CaptureTarget {
    val root = allowedRoot(resource)
    val targetId = try { root.windowId } finally { root.recycle() }
    val available = windows
    try {
      val target = available.firstOrNull { it.id == targetId } ?: error("window_unavailable")
      require(target.type == AccessibilityWindowInfo.TYPE_APPLICATION && target.isActive && target.displayId == Display.DEFAULT_DISPLAY) { "window_unavailable" }
      val display = getSystemService(DisplayManager::class.java).getDisplay(target.displayId) ?: error("display_unavailable")
      val size = Point(); display.getRealSize(size)
      val rect = Rect(); target.getBoundsInScreen(rect)
      require(!rect.isEmpty && rect.left >= 0 && rect.top >= 0 && rect.right <= size.x && rect.bottom <= size.y) { "window_geometry_unavailable" }
      val insets = getSystemService(WindowManager::class.java).maximumWindowMetrics.windowInsets.getInsetsIgnoringVisibility(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout())
      val interactionRect = Rect(rect)
      require(interactionRect.intersect(insets.left, insets.top, size.x - insets.right, size.y - insets.bottom)) { "window_geometry_unavailable" }
      val descriptions = available.map { window ->
        val bounds = Rect(); window.getBoundsInScreen(bounds)
        val windowRoot = window.root
        val app = try { windowRoot?.packageName?.toString() ?: "" } finally { windowRoot?.recycle() }
        if (window.id != targetId && window.displayId == target.displayId) {
          val intersects = Rect.intersects(bounds, rect)
          val passiveBar = window.type == AccessibilityWindowInfo.TYPE_SYSTEM && !window.isActive && !window.isFocused &&
            (bounds.bottom <= insets.top || bounds.top >= size.y - insets.bottom || bounds.right <= insets.left || bounds.left >= size.x - insets.right)
          // A whole-display frame is never taken while a permission/own UI or an
          // unidentifiable application could also be captured.
          if (scope == "display") {
            require((!protectedPackage(app) || passiveBar) && window.type != AccessibilityWindowInfo.TYPE_INPUT_METHOD &&
              window.type != AccessibilityWindowInfo.TYPE_ACCESSIBILITY_OVERLAY &&
              (window.type != AccessibilityWindowInfo.TYPE_APPLICATION || app.isNotEmpty()) &&
              !window.isActive && !window.isFocused) { "capture_scope_obstructed" }
          }
          if (intersects && window.layer > target.layer) require(passiveBar) { "window_obstructed" }
        }
        listOf(window.id, window.displayId, window.type, window.layer, bounds.flattenToString(), app, window.isActive, window.isFocused).joinToString(":")
      }.sorted().joinToString("|")
      val viewport = if (scope == "window") Viewport(rect.left, rect.top, rect.width(), rect.height()) else Viewport(0, 0, size.x, size.y)
      return CaptureTarget(resource, targetId, target.displayId, display.rotation, viewport,
        Viewport(interactionRect.left, interactionRect.top, interactionRect.width(), interactionRect.height()), scope, revision, descriptions,
        controller.observationIdentity().toString(), UUID.randomUUID().toString(), System.currentTimeMillis(), android.os.SystemClock.elapsedRealtime())
    } finally { available.forEach { it.recycle() } }
  }
  fun validateCaptureTarget(target: CaptureTarget) {
    val current = captureTarget(target.resource, target.scope)
    require(current.windowId == target.windowId && current.displayId == target.displayId && current.rotation == target.rotation &&
      current.viewport == target.viewport && current.interactionBounds == target.interactionBounds && current.revision == target.revision && current.windows == target.windows && current.identity == target.identity) { "window_changed" }
  }
  fun captureObservation(target: CaptureTarget, geometry: FrameGeometry, frameId: String, hash: String): JSONObject =
    JSONObject(target.identity).put("observationId", target.observationId).put("resourceId", target.resource).put("appId", target.resource)
      .put("windowId", target.windowId.toString()).put("observedUnixMs", target.observedUnixMs)
      .put("geometryRevision", target.revision.toString()).put("rotation", geometry.rotation)
      .put("viewport", JSONObject().put("left", geometry.viewport.left).put("top", geometry.viewport.top)
        .put("width", geometry.viewport.width).put("height", geometry.viewport.height))
      .put("width", geometry.width).put("height", geometry.height).put("frameId", frameId)
      .put("captureScope", target.scope).put("availability", "screenshot_only").put("partial", false)
      .put("frame", JSONObject().put("frameId", frameId).put("sha256", hash).put("mimeType", "image/jpeg")
        .put("width", geometry.width).put("height", geometry.height))

  fun capture(resource: String, scope: String, work: CommandWork, valid: () -> Boolean, callback: (Result<ExecutorCapture.Image>) -> Unit) {
    clearObservation()
    val target = captureTarget(resource, scope)
    capture.capture(target, work, valid, callback)
  }
  fun acceptCapturedFrame(image: ExecutorCapture.Image) {
    validateCaptureTarget(image.target)
    capturedFrame = JSONObject(image.observation.toString()); capturedTarget = image.target; capturedMono = image.target.observedMono
  }
  fun validateGesture(resource: String, arguments: JSONObject, precondition: JSONObject): CaptureTarget {
    val previous = capturedFrame ?: error("frame_required")
    val target = capturedTarget ?: error("frame_required")
    CapturePolicy.gesture(Build.VERSION.SDK_INT, target.scope, capturedMono, android.os.SystemClock.elapsedRealtime(), previous.getString("resourceId") == resource)
    for (key in listOf("observationId", "deviceId", "bootId", "controlSessionId", "resourceId", "appId", "windowId", "frameId", "geometryRevision", "rotation", "width", "height", "viewport")) {
      require(ExecutorWire.canonical(previous.get(key)) == ExecutorWire.canonical(precondition.get(key))) { "stale_frame" }
    }
    validateCaptureTarget(target)
    val geometry = frameGeometry(previous)
    fun checkPoint(x: Int, y: Int) {
      val (screenX, screenY) = geometry.displayPoint(x, y)
      val allowed = target.interactionBounds
      require(screenX >= allowed.left && screenY >= allowed.top && screenX < allowed.left + allowed.width && screenY < allowed.top + allowed.height) { "coordinate_in_system_region" }
    }
    checkPoint(coordinate(arguments, "x"), coordinate(arguments, "y"))
    when (arguments.getString("action")) {
      "tap" -> require(arguments.keys().asSequence().toSet() == setOf("action", "x", "y")) { "invalid_gesture_arguments" }
      "swipe" -> {
        require(arguments.keys().asSequence().toSet() == setOf("action", "x", "y", "endX", "endY", "durationMs")) { "invalid_gesture_arguments" }
        checkPoint(coordinate(arguments, "endX"), coordinate(arguments, "endY"))
        require(arguments.getLong("durationMs") in 100..1000) { "invalid_gesture_duration" }
      }
      else -> error("unsupported_gesture")
    }
    // Magnification/explore-by-touch transform coordinates after the API, so
    // those modes cannot consume a frame's untransformed pixel geometry.
    val untransformed = if (Build.VERSION.SDK_INT >= 34) magnificationController.magnificationConfig?.let { !it.isActivated } == true else false
    require(untransformed &&
      !getSystemService(android.view.accessibility.AccessibilityManager::class.java).isTouchExplorationEnabled) { "gesture_transform_active" }
    return target
  }
  private fun frameGeometry(frame: JSONObject): FrameGeometry {
    val viewport = frame.getJSONObject("viewport")
    return FrameGeometry(frame.getInt("width"), frame.getInt("height"), frame.getInt("rotation"),
      Viewport(viewport.getInt("left"), viewport.getInt("top"), viewport.getInt("width"), viewport.getInt("height")))
  }
  private fun coordinate(arguments: JSONObject, key: String): Int {
    val value = arguments.getLong(key)
    require(value in 0..4095) { "coordinate_out_of_bounds" }
    return value.toInt()
  }
  fun gesture(resource: String, arguments: JSONObject, precondition: JSONObject, work: CommandWork, valid: () -> Boolean,
              beforeDispatch: () -> Unit, callback: (String, String?) -> Unit) {
    val target = validateGesture(resource, arguments, precondition)
    capture.secureProbe(target, work, valid) { result ->
      if (!work.active || !valid()) return@secureProbe
      val failure = result.exceptionOrNull()
      if (failure != null) { callback("rejected", failure.message); return@secureProbe }
      var started = false
      try {
        validateGesture(resource, arguments, precondition)
        require(work.active && valid()) { "gesture_cancelled" }
        val geometry = frameGeometry(requireNotNull(capturedFrame))
        val start = geometry.displayPoint(coordinate(arguments, "x"), coordinate(arguments, "y"))
        val path = Path().apply { moveTo(start.first, start.second) }
        val swipe = arguments.getString("action") == "swipe"
        if (swipe) geometry.displayPoint(coordinate(arguments, "endX"), coordinate(arguments, "endY")).let { path.lineTo(it.first, it.second) }
        val duration = if (swipe) arguments.getLong("durationMs") else 60L
        val description = GestureDescription.Builder().setDisplayId(target.displayId)
          .addStroke(GestureDescription.StrokeDescription(path, 0, duration)).build()
        beforeDispatch() // Persist started immediately before dispatch, after every check.
        started = true
        val accepted = dispatchGesture(description, object : GestureResultCallback() {
          override fun onCompleted(gestureDescription: GestureDescription) { if (work.active && valid()) callback("succeeded", null) }
          override fun onCancelled(gestureDescription: GestureDescription) { if (work.active && valid()) callback("unknown_outcome", "gesture_cancelled") }
        }, null)
        if (!accepted && work.active && valid()) callback("failed", "gesture_not_dispatched")
      } catch (error: Exception) {
        if (work.active && valid()) callback(if (started) "unknown_outcome" else "rejected", error.message)
      }
    }
  }
  companion object { @Volatile var instance: ExecutorAccessibilityService? = null; private set }
}
