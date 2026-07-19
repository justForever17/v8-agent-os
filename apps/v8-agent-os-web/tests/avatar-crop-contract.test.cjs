const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const repoRoot = path.resolve(__dirname, "../../..");
const read = (relativePath) => fs.readFileSync(path.join(repoRoot, relativePath), "utf8");

test("all four editable avatar surfaces use a user-positioned crop", () => {
  const sharedCropper = read("packages/product-ui/src/SquareImageCropper.tsx");
  const webSettings = read("apps/v8-agent-os-web/src/components/settings/SettingsDialog.tsx");
  const supervisor = read("apps/v8-agent-os-admin/src/app/admin/(dashboard)/supervisor/page.tsx");
  const subagents = read("apps/v8-agent-os-admin/src/app/admin/(dashboard)/subagents/page.tsx");
  const phoneProfile = read("apps/v8-agent-os-phone/src/components/chat/ProfileMenuOverlay.tsx");
  const phoneSettings = read("apps/v8-agent-os-phone/src/screens/SettingsScreen.tsx");
  const phoneCropper = read("apps/v8-agent-os-phone/src/components/ui/AvatarCropModal.tsx");

  assert.match(sharedCropper, /onPointerMove=\{handlePointerMove\}/);
  assert.match(sharedCropper, /context\.drawImage\(/);
  assert.match(webSettings, /<AvatarCropDialog/);
  assert.match(supervisor, /<AvatarCropDialog/);
  assert.match(subagents, /<AvatarCropDialog/);

  assert.match(phoneProfile, /<AvatarCropModal/);
  assert.doesNotMatch(phoneSettings, /AvatarCropModal|pickAvatar|launchImageLibraryAsync/);
  assert.match(phoneCropper, /PanResponder\.create/);
  assert.match(phoneCropper, /touches\.length >= 2/);
  assert.match(phoneCropper, /ImageManipulator\.manipulate/);
  assert.match(phoneCropper, /context\.crop\(/);
  assert.doesNotMatch(phoneProfile, /allowsEditing:\s*true/);
});

test("Phone avatar truth refreshes and caches immutable avatar URLs locally", () => {
  const session = read("apps/v8-agent-os-phone/src/providers/app-session.tsx");
  const cache = read("apps/v8-agent-os-phone/src/lib/profile-avatar-cache.ts");

  assert.match(session, /AppState\.addEventListener\("change"/);
  assert.match(session, /10_000/);
  assert.match(session, /cacheProfileAvatar\(source\)/);
  assert.match(cache, /FileSystem\.downloadAsync/);
  assert.match(cache, /userAvatarCache/);
  assert.match(cache, /avatar-\$\{stableHash\(normalizedSource\)\}\.webp/);
});

test("Phone empty conversations use the time greeting without a robot ornament", () => {
  const chatScreen = read("apps/v8-agent-os-phone/src/screens/ChatScreen.tsx");
  const chatWindow = read("apps/v8-agent-os-phone/src/components/chat/ChatWindow.tsx");

  assert.match(chatScreen, /title: getDayGreeting\(locale\)/);
  assert.match(chatScreen, /const greetingEmptyState = projection\.projectedMessages\.length === 0/);
  assert.doesNotMatch(chatWindow, /icon: "robot-happy-outline"/);
});
