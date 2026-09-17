const { withAndroidManifest, createRunOncePlugin } = require("expo/config-plugins");

// Services and resources belong to the tracked local module, merged by Gradle.
// No foreground-service type, boot receiver, or automatic permission grant.
function withDeviceExecutor(config) {
  return withAndroidManifest(config, (result) => {
    const permissions = result.modResults.manifest["uses-permission"] ||= [];
    if (!permissions.some((item) => item.$["android:name"] === "android.permission.POST_NOTIFICATIONS")) {
      permissions.push({ $: { "android:name": "android.permission.POST_NOTIFICATIONS" } });
    }
    return result;
  });
}
module.exports = createRunOncePlugin(withDeviceExecutor, "v8-device-executor", "1.0.0");
