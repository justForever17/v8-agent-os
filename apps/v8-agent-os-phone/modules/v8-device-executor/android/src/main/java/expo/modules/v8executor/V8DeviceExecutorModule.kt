package expo.modules.v8executor

import android.content.Intent
import android.provider.Settings
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition

class V8DeviceExecutorModule : Module() {
  private val controller get() = ExecutorController.get(requireNotNull(appContext.reactContext))
  override fun definition() = ModuleDefinition {
    Name("V8DeviceExecutor")
    Events("onState")
    OnStartObserving { controller.onMain { controller.onState = { sendEvent("onState", it) } } }
    OnStopObserving { controller.onMain { controller.onState = null } }
    AsyncFunction("getState") { controller.onMain { controller.state() } }
    AsyncFunction("enroll") { ticket: String, authorityId: String, baseUrl: String, name: String, profileAuthorityKey: String, allowedApps: List<String> ->
      controller.enroll(ticket, authorityId, baseUrl, name, profileAuthorityKey, allowedApps)
      controller.onMain { controller.state() }
    }
    AsyncFunction("setAllowedApps") { apps: List<String> -> controller.onMain { controller.setAllowedApps(apps); controller.state() } }
    AsyncFunction("acknowledgeGrantRevision") { revision: Long -> controller.onMain { controller.acknowledgeGrantRevision(revision); controller.state() } }
    AsyncFunction("enable") { controller.onMain { controller.enable(); controller.state() } }
    AsyncFunction("stop") { controller.onMain { controller.stop(); controller.state() } }
    AsyncFunction("disable") { controller.onMain { controller.stop("disabled"); controller.state() } }
    AsyncFunction("revoke") { controller.revoke(); controller.onMain { controller.state() } }
    AsyncFunction("forgetProfile") { profileAuthorityKey: String ->
      if (controller.onMain { controller.forgetProfile(profileAuthorityKey) }) controller.revoke()
      controller.onMain { controller.state() }
    }
    AsyncFunction("openAccessibilitySettings") {
      controller.onMain {
        requireNotNull(appContext.reactContext).startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
      }
    }
  }
}
