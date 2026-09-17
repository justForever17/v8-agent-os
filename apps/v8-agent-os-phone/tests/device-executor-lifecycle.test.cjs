const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const ts = require("typescript");
const tick = () => new Promise(resolve => setImmediate(resolve));
const root = path.resolve(__dirname, "..");

function load(file, mocks) {
  const code = ts.transpileModule(fs.readFileSync(path.join(root, file), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const module = { exports: {} };
  vm.runInThisContext(`(function(require,module,exports){${code}\n})`, { filename: file })(name => {
    assert.ok(Object.hasOwn(mocks, name), `Unexpected dependency: ${name}`);
    return mocks[name];
  }, module, module.exports);
  return module.exports;
}

function screen(forget) {
  const profiles = ["A", "B"].map(id => ({ id, label: id, instanceId: "same-instance", principalId: "same-user",
    adminBaseUrl: "https://engine.invalid", lastUsedAt: "" }));
  const states = [], events = []; let cursor = 0, confirmation;
  const jsx = (type, props) => ({ type, props });
  const component = load("src/screens/ConnectScreen.tsx", {
    "react": { useCallback: fn => fn, useEffect() {}, useMemo: fn => fn(), useState: initial => {
      const id = cursor++; if (!(id in states)) states[id] = initial;
      return [states[id], next => { states[id] = typeof next === "function" ? next(states[id]) : next; }];
    } },
    "react/jsx-runtime": { jsx, jsxs: jsx, Fragment: "Fragment" },
    "react-native": { ActivityIndicator: "ActivityIndicator", FlatList: "FlatList", Modal: "Modal", Pressable: "Pressable",
      Text: "Text", TextInput: "TextInput", View: "View", StyleSheet: { create: x => x },
      Alert: { alert: (_label, _message, actions) => { confirmation = actions.find(a => a.style === "destructive"); } } },
    "expo-router": { router: {} }, "@react-navigation/native": { useIsFocused: () => false },
    "react-native-safe-area-context": { SafeAreaView: "SafeAreaView" }, "@expo/vector-icons": { MaterialCommunityIcons: "Icon" },
    "@/src/components/layout/PhoneTopbar": { PhoneTopbar: "PhoneTopbar" },
    "@/src/components/connections/PeerConversation": { PeerConversation: "PeerConversation" },
    "@/src/components/connections/ConfigDistributionPanel": { ConfigDistributionPanel: "ConfigDistributionPanel" },
    "@/src/hooks/use-go-home-to-chat": { useGoHomeToChat: () => () => {} },
    "@/src/hooks/use-app-visibility": { useAppVisibility: () => false },
    "@/src/lib/admin-connection-profiles": { readAdminConnectionProfiles: async () => profiles,
      updateAdminConnectionProfiles: async update => { events.push("remove"); return update(profiles); } },
    "@/src/lib/supervisor-peers": {}, "@/src/lib/mobile-storage": {},
    "@/src/lib/device-executor": { deviceExecutor: { forgetProfile: key => { events.push(["stop", key]); return forget(key); } } },
    "@/src/lib/phone-identity": { phoneAuthorityKey: ({ instanceId, principalId, profileId }) => JSON.stringify([instanceId, principalId, profileId]) },
    "@/src/providers/app-session": { useAppSession: () => ({ status: "authenticated", activeProfileId: "B" }) },
    "@/src/providers/ui-prefs": { useUiPrefs: () => ({ t: key => key, colors: {} }) },
  }).default;
  function nodes(node) { return !node || typeof node !== "object" ? [] : [node, ...[node.props?.children].flat(Infinity).flatMap(nodes)]; }
  const render = () => { cursor = 0; return nodes(component()); };
  const list = render().find(node => node.type === "FlatList");
  const row = nodes(list.props.renderItem({ item: { kind: "profile", value: profiles[0] } }));
  row.find(node => node.type === "Pressable" && node.props.accessibilityLabel === "A").props.onPress();
  render().find(node => node.type === "Pressable" && nodes(node).some(child => child.type === "Text" && child.props.children === "phone.devices.remove")).props.onPress();
  confirmation.onPress();
  return { events, render };
}

test("removing inactive bound profile stops its exact authority before deleting, without targeting the active profile", async () => {
  let complete; const task = screen(() => new Promise(resolve => { complete = resolve; }));
  assert.deepEqual(task.events, [["stop", '["same-instance","same-user","A"]']]);
  complete(); await tick();
  assert.deepEqual(task.events, [["stop", '["same-instance","same-user","A"]'], "remove"]);
});

test("failure to stop the native executor preserves the connection profile for recovery", async () => {
  const task = screen(async () => { throw new Error("native_stop_failed"); });
  await tick();
  assert.equal(task.events.length, 1);
  assert.ok(task.render().some(node => node.type === "Text" && String(node.props.children).includes("native_stop_failed")));
});

test("unsupported platforms do not load or arm native code", async () => {
  const facade = load("src/lib/device-executor.ts", {
    "react-native": { Platform: { OS: "ios" } },
    "expo-modules-core": { requireOptionalNativeModule: () => assert.fail("Native module loaded on iOS") },
  });
  assert.equal((await facade.deviceExecutor.getState()).supported, false);
  assert.equal((await facade.deviceExecutor.stop()).enabled, false);
});

test("window capture grants never silently include full-display observation", () => {
  const facade = load("src/lib/device-executor.ts", {
    "react-native": { Platform: { OS: "ios" } },
    "expo-modules-core": {},
  });
  const window = facade.executorGrants({ windowCaptureAvailable: true, fullDisplayCapture: false }, ["fixture.a"]);
  assert.deepEqual(window, ["android.observe", "android.action", "android.capture"].map(capability => ({ capability, resourceId: "fixture.a" })));
  const old = facade.executorGrants({ windowCaptureAvailable: false, fullDisplayCapture: false }, ["fixture.a"]);
  assert.equal(old.some(grant => grant.capability === "android.capture"), false);
  const consented = facade.executorGrants({ windowCaptureAvailable: false, fullDisplayCapture: true }, ["fixture.a"]);
  assert.deepEqual(consented.filter(grant => grant.capability === "android.capture"), [
    { capability: "android.capture", resourceId: "fixture.a" }, { capability: "android.capture", resourceId: "display" },
  ]);
});

test("enrollment refuses a changed ticket origin before any native credential exchange", async () => {
  let nativeCalls = 0;
  const facade = load("src/lib/device-executor.ts", {
    "react-native": { Platform: { OS: "android" } },
    "expo-modules-core": { requireOptionalNativeModule: () => ({ enroll: () => { nativeCalls++; } }) },
  });
  await assert.rejects(facade.enrollExecutor(async () => Response.json({ baseUrl: "https://other.invalid" }), {
    baseUrl: "https://engine.invalid", authorityKey: "A", name: "Fixture", allowedApps: ["test.fixture"],
  }), /enrollment_origin_changed/);
  assert.equal(nativeCalls, 0);
});
