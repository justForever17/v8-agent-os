package expo.modules.v8executor

import android.accessibilityservice.AccessibilityService
import android.app.KeyguardManager
import android.content.Context
import android.content.BroadcastReceiver
import android.content.Intent
import android.content.IntentFilter
import android.os.Build
import android.graphics.Rect
import android.os.Bundle
import android.view.KeyEvent
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

class ExecutorAccessibilityService : AccessibilityService() {
  private data class Node(val info: AccessibilityNodeInfo, val fingerprint: String)
  private val nodes = mutableMapOf<String, Node>()
  private var observation: JSONObject? = null
  private var revision = 0L
  private var observationMono = 0L
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
    clearObservation(); instance = null; controller.permissionChanged(false); super.onDestroy()
  }
  override fun onKeyEvent(event: KeyEvent): Boolean {
    if (event.action == KeyEvent.ACTION_DOWN) controller.stop("local_key_interaction")
    return false
  }
  override fun onAccessibilityEvent(event: AccessibilityEvent) {
    revision++
    if (!controller.isEnabled()) return
    if (getSystemService(KeyguardManager::class.java).isKeyguardLocked) { controller.stop("device_locked"); return }
    if (event.eventType == AccessibilityEvent.TYPE_TOUCH_INTERACTION_START || event.eventType == AccessibilityEvent.TYPE_GESTURE_DETECTION_START) {
      controller.stop("local_touch_interaction")
    } else if (!controller.isApplying() && event.eventType in setOf(AccessibilityEvent.TYPE_VIEW_CLICKED,
        AccessibilityEvent.TYPE_VIEW_LONG_CLICKED, AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED, AccessibilityEvent.TYPE_VIEW_SCROLLED)) {
      controller.stop("local_ui_interaction")
    }
  }

  fun clearObservation() { nodes.values.forEach { it.info.recycle() }; nodes.clear(); observation = null }
  private fun allowedRoot(resource: String): AccessibilityNodeInfo {
    require(!getSystemService(KeyguardManager::class.java).isKeyguardLocked) { "device_locked" }
    require(resource in controller.allowedApps()) { "app_not_allowed" }
    require(resource != packageName && resource != "android" && resource != "com.android.settings" &&
      !resource.contains("permissioncontroller")) { "protected_ui" }
    val root = rootInActiveWindow ?: error("window_unavailable")
    if (root.packageName?.toString() != resource) { root.recycle(); error("window_changed") }
    require(root.refresh()) { "window_unavailable" }
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
  companion object { @Volatile var instance: ExecutorAccessibilityService? = null; private set }
}
