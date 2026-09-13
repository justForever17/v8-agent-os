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

test("playlist wallpaper has one player and transparent sidebar in both themes", () => {
  const settings = read("src/components/settings/BackgroundPlaylistSettings.tsx");
  const provider = read("src/components/providers/PersonalizationProvider.tsx");
  const styles = read("src/app/globals.css");
  const sidebar = read("src/components/layout/Sidebar.tsx");
  const chatWindow = read("src/components/chat/ChatWindow.tsx");

  assert.match(settings, /data-testid="background-preview"/);
  assert.match(settings, /data-testid="background-video-summary"/);
  assert.doesNotMatch(settings, /<video/);
  assert.match(settings, /backgroundSize: item.fit/);
  assert.match(provider, /root\.dataset\.v8Wallpaper = "active"/);
  assert.match(provider, /image\.onload/);
  assert.match(provider, /<video/);
  assert.match(provider, /muted=\{videoMuted\}/);
  assert.doesNotMatch(provider, /resolvedTheme/);
  // Playback is generation-bound. Errors advance or become visible; an old
  // retry timer must not replace the selected playlist item.
  assert.equal((provider.match(/<video\b/g) || []).length, 1);
  assert.match(provider, /loop=\{items.length === 1\}/);
  assert.match(provider, /onEnded=\{\(\) => advance\(1, selection.serial\)\}/);
  assert.match(provider, /selectionRef.current.serial !== selection.serial/);
  assert.match(provider, /enabled && ready && visible && !paused/);
  assert.match(provider, /video\.pause\(\)/);
  assert.match(provider, /preload="metadata"/);
  assert.match(provider, /playbackFailed/);
  assert.match(provider, /failed.current.clear\(\)/);
  assert.doesNotMatch(provider, /v8-personalization-wallpaper-overlay/);
  assert.match(styles, /html\.light\[data-v8-wallpaper="active"\]/);
  assert.match(styles, /html\.dark\[data-v8-wallpaper="active"\]/);
  assert.match(styles, /background-size: var\(--v8-wallpaper-fit, cover\)/);
  assert.match(styles, /data-v8-wallpaper-kind="video"[\s\S]*backdrop-filter: none !important/);
  assert.match(styles, /data-v8-wallpaper-kind="video"[\s\S]*transition: none/);
  assert.match(styles, /\.v8-chat-viewport-surface/);
  assert.match(chatWindow, /v8-chat-viewport-surface/);
  assert.match(sidebar, /group\/sidebar relative z-20 hidden h-full/);
  assert.match(sidebar, /v8-sidebar-surface/);
  assert.match(styles, /\.v8-sidebar-surface\s*\{[^}]*background:\s*transparent/);
});

test("topbar sound control belongs only to an active MP4 wallpaper", () => {
  const topbar = read("src/components/layout/Topbar.tsx");
  const toggle = read("src/components/layout/BackgroundVideoSoundToggle.tsx");
  const provider = read("src/components/providers/PersonalizationProvider.tsx");
  const voiceCard = read("src/components/chat/VoiceCard.tsx");

  assert.match(topbar, /<BackgroundVideoSoundToggle/);
  assert.match(toggle, /if \(!available\) return null/);
  assert.match(toggle, /toggleMuted/);
  assert.match(provider, /video\.volume = 0\.65/);
  assert.doesNotMatch(voiceCard, /isVoiceEnabled|useVoiceStore|Auto-play prevented/);
});

test("large MP4 wallpaper uploads stream through the Web bridge", () => {
  const settings = read("src/components/settings/BackgroundPlaylistSettings.tsx");
  const upload = read("src/app/api/user-background-upload/route.ts");

  assert.match(settings, /headers: \{ "content-type": file\.type \}/);
  assert.match(settings, /body: file/);
  assert.match(upload, /"x-v8-upload-mode": "raw"/);
  assert.match(upload, /body: req\.body/);
  assert.match(upload, /duplex: "half"/);
  assert.doesNotMatch(upload, /req\.formData\(\)/);
});

test("profile refreshes across clients while retaining the canonical profile", () => {
  const profile = read("src/hooks/use-client-profile.ts");
  assert.match(profile, /visibilitychange/);
  assert.match(profile, /window\.addEventListener\("focus"/);
  assert.match(profile, /10_000/);
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

test("chat markdown keeps list markers inside user bubbles", () => {
  const renderer = read("src/components/chat/MarkdownRenderer.tsx");

  assert.match(renderer, /ol: \(\{ children \}:[\s\S]*?list-decimal/);
  assert.match(renderer, /ul: \(\{ children \}:[\s\S]*?list-disc/);
  assert.match(renderer, /marker:text-current/);
});
