package expo.modules.v8executor

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import okhttp3.OkHttpClient
import okhttp3.Request

/** Explicit local UI for granting only the test APK's system permissions. */
class ExecutorTestActivity : Activity() {
  override fun onCreate(savedInstanceState: Bundle?) {
    super.onCreate(savedInstanceState)
    val layout = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(32, 80, 32, 32) }
    layout.addView(TextView(this).apply { text = "V8 Executor Verification\nSynthetic fixture only. The existing Phone account and data are not used."; textSize = 20f })
    layout.addView(Button(this).apply { text = "Open accessibility settings"; setOnClickListener { startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) } })
    layout.addView(Button(this).apply { text = "Allow Stop notification"; setOnClickListener {
      if (Build.VERSION.SDK_INT >= 33) requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 100)
    } })
    layout.addView(Button(this).apply { text = "Stop executor"; setOnClickListener { ExecutorController.get(this@ExecutorTestActivity).stop() } })
    layout.addView(Button(this).apply { text = "Connect synthetic fixture Engine"; setOnClickListener { connectFixture() } })
    setContentView(layout)
    when (intent.getStringExtra("fixtureCommand")) {
      "connect" -> connectFixture()
      "resume" -> resumeFixture()
      "stop" -> ExecutorController.get(this).stop()
      "status" -> android.util.Log.i("V8ExecutorFixture", "probe_enabled=" + ExecutorController.get(this).state()["enabled"])
    }
  }
  private fun resumeFixture() {
    val controller = ExecutorController.get(this)
    controller.onState = { state -> android.util.Log.i("V8ExecutorFixture", "state=" + state["status"] + ";reason=" + state["lastError"]) }
    Thread {
      try {
        val deadline = android.os.SystemClock.elapsedRealtime() + 15_000
        while (ExecutorAccessibilityService.instance == null && android.os.SystemClock.elapsedRealtime() < deadline) Thread.sleep(100)
        controller.onMain {
          require(controller.state()["profileAuthorityKey"] == "synthetic-owner") { "not_synthetic_binding" }
          controller.enable()
          startActivity(Intent().setClassName("com.v8agentos.executorfixture", "com.v8agentos.executorfixture.FixtureActivity").addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP))
        }
      } catch (error: Exception) { android.util.Log.e("V8ExecutorFixture", "synthetic_resume_failed:" + error.javaClass.simpleName) }
    }.start()
  }
  private fun connectFixture() {
    val controller = ExecutorController.get(this)
    controller.onState = { state -> android.util.Log.i("V8ExecutorFixture", "state=" + state["status"] + ";reason=" + state["lastError"]) }
    Thread {
      try {
        val serviceDeadline = android.os.SystemClock.elapsedRealtime() + 15_000
        while (ExecutorAccessibilityService.instance == null && android.os.SystemClock.elapsedRealtime() < serviceDeadline) Thread.sleep(100)
        require(ExecutorAccessibilityService.instance != null) { "requires_accessibility_permission" }
        val state = controller.onMain { controller.state() }
        if (state["deviceId"] != null) {
          require(state["profileAuthorityKey"] == "synthetic-owner") { "not_synthetic_binding" }
          require(controller.revoke()) { "fixture_revocation_pending" }
        }
        val ticket = OkHttpClient().newCall(Request.Builder().url("https://localhost:9533/fixture/ticket").build()).execute().use {
          require(it.isSuccessful) { "fixture_unavailable" }; ExecutorWire.parse(requireNotNull(it.body).string())
        }
        controller.enroll(ticket.getString("ticket"), ticket.getString("authorityId"), ticket.getString("baseUrl"),
          "Android synthetic verification", "synthetic-owner", listOf("com.v8agentos.executorfixture"))
        controller.onMain {
          controller.enable()
          startActivity(Intent().setClassName("com.v8agentos.executorfixture", "com.v8agentos.executorfixture.FixtureActivity"))
        }
        android.util.Log.i("V8ExecutorFixture", "synthetic_session_started")
      } catch (error: Exception) {
        android.util.Log.e("V8ExecutorFixture", "synthetic_session_failed:" + error.javaClass.simpleName)
        if (error is javax.net.ssl.SSLException) android.util.Log.e("V8ExecutorFixture", "TLS diagnostic: " + error.message + "; cause=" + error.cause?.message)
        runOnUiThread { (findViewById<android.view.ViewGroup>(android.R.id.content).getChildAt(0) as LinearLayout)
          .addView(TextView(this).apply { text = "Fixture connection failed: " + error.javaClass.simpleName }) }
      }
    }.start()
  }
}
