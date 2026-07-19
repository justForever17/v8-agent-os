const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const appRoot = path.resolve(__dirname, "..");
const read = (relativePath) => fs.readFileSync(path.join(appRoot, relativePath), "utf8");

test("avatar upload uses durable client media and has a visual fallback", () => {
  const upload = read("src/app/api/user-avatar-upload/route.ts");
  const settings = read("src/components/settings/SettingsDialog.tsx");
  const avatarProxy = read("src/app/api/avatar/route.ts");

  assert.match(upload, /\/client\/user-avatar-upload/);
  assert.match(settings, /<AvatarFallback>/);
  assert.match(settings, /data\.user \|\|/);
  assert.doesNotMatch(settings, /customAvatarUrl|customBackgroundUrl/);
  assert.match(settings, /avatarFileInputRef\.current\?\.click\(\)/);
  assert.match(avatarProxy, /allowedKinds: \["avatar"\]/);
});

test("light wallpaper uses center cropping and never changes dark theme", () => {
  const settings = read("src/components/settings/SettingsDialog.tsx");
  const provider = read("src/components/providers/PersonalizationProvider.tsx");
  const styles = read("src/app/globals.css");
  const sidebar = read("src/components/layout/Sidebar.tsx");
  const chatWindow = read("src/components/chat/ChatWindow.tsx");

  assert.match(settings, /data-testid="light-background-preview"/);
  assert.match(settings, /backgroundSize: "cover"/);
  assert.match(provider, /root\.dataset\.v8Wallpaper = "active"/);
  assert.match(provider, /image\.onload/);
  assert.match(provider, /root\.style\.getPropertyValue\("--v8-wallpaper-image"\) !== cssValue/);
  assert.match(styles, /html\.light\[data-v8-wallpaper="active"\]/);
  assert.match(styles, /background-size: cover/);
  assert.match(styles, /\.v8-chat-viewport-surface/);
  assert.match(chatWindow, /v8-chat-viewport-surface/);
  assert.match(sidebar, /group\/sidebar relative z-20 hidden h-full/);
  assert.doesNotMatch(styles, /html\.dark\[data-v8-wallpaper="active"\]/);
});

test("wallpaper bootstrap and profile projection share one appearance truth", () => {
  const layout = read("src/app/layout.tsx");
  const profile = read("src/hooks/use-client-profile.ts");
  const personalization = read("src/lib/personalization.ts");

  assert.match(layout, /buildPersonalizationBootstrapScript/);
  assert.match(layout, /<PersonalizationProvider>/);
  assert.match(profile, /normalizeAppearance\(left\?\.appearance\)/);
  assert.match(profile, /sessionFieldsMatch\(next, sessionProfile\)/);
  assert.match(personalization, /PERSONALIZATION_STORAGE_KEY/);
});
