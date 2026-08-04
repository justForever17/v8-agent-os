const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const petRoot = path.resolve(__dirname, '..');

test('desktop pet auto-connects with bounded retries and loopback-only local server', () => {
  const appSource = fs.readFileSync(path.join(petRoot, 'src', 'App.tsx'), 'utf8');
  const serverSource = fs.readFileSync(path.join(petRoot, 'server.ts'), 'utf8');
  const mainSource = fs.readFileSync(path.join(petRoot, 'electron', 'main.cjs'), 'utf8');
  const voiceSmokeSource = fs.readFileSync(path.join(petRoot, 'test', 'v8os_voice_live_smoke.ts'), 'utf8');

  assert.match(appSource, /ensureLocalSession/);
  assert.match(appSource, /const retryDelays = \[500, 1000, 2000, 4000, 5000\]/);
  assert.match(serverSource, /httpServer\.listen\(PORT, "127\.0\.0\.1"/);
  assert.doesNotMatch(serverSource, /httpServer\.listen\(PORT, "0\.0\.0\.0"/);
  assert.match(serverSource, /v8-desktop-server-ready/);
  assert.match(serverSource, /v8-agent-os-desktop-pet/);
  assert.match(serverSource, /configuredPort === undefined \? 0 : Number\(configuredPort\)/);
  assert.match(mainSource, /configuredLocalServerPort === undefined[\s\S]{0,40}\? 0/);
  assert.match(mainSource, /V8_DESKTOP_PORT: String\(REQUESTED_LOCAL_SERVER_PORT\)/);
  assert.match(mainSource, /REQUESTED_LOCAL_SERVER_PORT !== 0 && port !== REQUESTED_LOCAL_SERVER_PORT/);
  assert.match(mainSource, /const baseUrl = `http:\/\/127\.0\.0\.1:\$\{port\}`/);
  assert.match(mainSource, /resolve\(transport\)/);
  assert.match(mainSource, /STABLE_RENDERER_ENTRY_URL/);
  assert.match(mainSource, /installStableRendererProtocol/);
  assert.match(mainSource, /writeDesktopPetProcessDescriptor\(transport\)/);
  assert.match(mainSource, /descriptor\.serverPid = transport\.serverPid/);
  assert.match(mainSource, /current\?\.descriptorId === DESKTOP_PET_DESCRIPTOR_ID/);
  assert.match(mainSource, /await mainWindow\.loadURL\(entry\.value\)/);
  assert.doesNotMatch(mainSource, /V8_DESKTOP_PORT[^\n]*3000/);
  assert.doesNotMatch(voiceSmokeSource, /127\.0\.0\.1:3000/);
  assert.match(voiceSmokeSource, /V8_CYBERCORE_USE_PROXY=1 requires V8_CYBERCORE_PROXY_BASE/);
  assert.match(mainSource, /stdio: \['ignore', 'pipe', 'pipe', 'ipc'\]/);
  assert.match(mainSource, /verifyBundledServer/);
  assert.match(mainSource, /isTrustedRendererUrl\(rendererUrl, DEVELOPMENT_TRANSPORT\)/);
  assert.doesNotMatch(mainSource, /url\.startsWith\('http:\/\/localhost:/);
  assert.doesNotMatch(mainSource, /url === 'about:blank'/);
  assert.doesNotMatch(mainSource, /LOCAL_SERVER_URL/);
  assert.doesNotMatch(mainSource, /setBackgroundColor\('#0f172a'\)/);
});

test('desktop pet window exposes the V8 product title', () => {
  const html = fs.readFileSync(path.join(petRoot, 'index.html'), 'utf8');
  const serverSource = fs.readFileSync(path.join(petRoot, 'server.ts'), 'utf8');
  const transportSource = fs.readFileSync(path.join(petRoot, 'lib', 'stable-renderer-transport.cjs'), 'utf8');
  assert.match(html, /<title>V8 Agent OS<\/title>/);
  assert.doesNotMatch(html, /Google AI Studio/);
  assert.doesNotMatch(html, /http-equiv="Content-Security-Policy"/);
  assert.match(serverSource, /desktopContentSecurityPolicy\(boundPort\)/);
  assert.match(transportSource, /productionRendererContentSecurityPolicy/);
  assert.match(transportSource, /connect-src 'self'/);
  assert.match(transportSource, /object-src 'none'/);
});

test('managed desktop pet follows unexpected Shell process loss but tolerates preview restart leases', () => {
  const mainSource = fs.readFileSync(path.join(petRoot, 'electron', 'main.cjs'), 'utf8');
  const watchdogSource = fs.readFileSync(path.join(petRoot, 'lib', 'shell-lifecycle-watchdog.cjs'), 'utf8');

  assert.match(mainSource, /createShellLifecycleWatchdog/);
  assert.match(mainSource, /safeShutdown\(\{ source: event\.reason \}\)/);
  assert.match(mainSource, /function finalizeShutdown[\s\S]*removeOwnedDesktopPetProcessDescriptor\(\)[\s\S]*app\.exit\(0\)/);
  assert.match(watchdogSource, /shell-restart\.json/);
  assert.match(watchdogSource, /preview_rebuild/);
  assert.match(watchdogSource, /shell_process_exited/);
});

test('desktop pet settings and shutdown use the Shell control contract', () => {
  const mainSource = fs.readFileSync(path.join(petRoot, 'electron', 'main.cjs'), 'utf8');
  const preloadSource = fs.readFileSync(path.join(petRoot, 'electron', 'preload.cjs'), 'utf8');

  assert.match(mainSource, /SHELL_SETTINGS_DEEP_LINK = 'v8os:\/\/open\/admin\/desktop-pet'/);
  assert.match(mainSource, /shellControlClient\?\.send\('open-settings'\)/);
  assert.doesNotMatch(mainSource, /v8-desktop:open-admin'[\s\S]{0,180}shell\.openExternal\(url\)/);
  assert.match(preloadSource, /shutdownReady/);
  assert.match(preloadSource, /onActiveSession/);
});

test('desktop pet hot-applies canonical config changes through the Admin BFF', () => {
  const mainSource = fs.readFileSync(path.join(petRoot, 'electron', 'main.cjs'), 'utf8');
  const preloadSource = fs.readFileSync(path.join(petRoot, 'electron', 'preload.cjs'), 'utf8');
  const appSource = fs.readFileSync(path.join(petRoot, 'src', 'App.tsx'), 'utf8');
  const petSource = fs.readFileSync(path.join(petRoot, 'src', 'components', 'CyberPet.tsx'), 'utf8');

  assert.match(mainSource, /createCanonicalConfigWatcher/);
  assert.match(mainSource, /v8-desktop:config-changed/);
  assert.match(preloadSource, /onDesktopPetConfigChanged/);
  assert.match(appSource, /onDesktopPetConfigChanged/);
  assert.match(appSource, /getDesktopPetConfig/);
  assert.match(appSource, /glowIntensity/);
  assert.match(appSource, /preset === 'energy'/);
  assert.match(appSource, /voiceRules !== null/);
  assert.doesNotMatch(appSource, /return normalized\.length \? normalized : DEFAULT_V8_EVENT_RULES/);
  assert.match(petSource, /colorWithAlpha/);
  assert.match(petSource, /settings\.glowIntensity/);
});

test('desktop pet event rules use structured event ids and keep legacy text read-only', () => {
  const appSource = fs.readFileSync(path.join(petRoot, 'src', 'App.tsx'), 'utf8');

  assert.match(appSource, /candidate\.event === activity\.event/);
  assert.match(appSource, /expandLegacyDesktopPetEvents/);
  assert.doesNotMatch(appSource, /haystack\.includes\(candidate\.match/);
});

test('desktop pet terminal events retry the authoritative snapshot before system TTS', () => {
  const appSource = fs.readFileSync(path.join(petRoot, 'src', 'App.tsx'), 'utf8');

  assert.match(appSource, /const retryDelays = \[0, 180, 420, 800\]/);
  assert.match(appSource, /v8PendingAssistantBaselineRef\.current\.get\(conversationId\)/);
  assert.match(appSource, /baseline && latestIdentity === baseline/);
  assert.match(appSource, /audioPlayed = audioPlayed \|\| v8LastSnapshotAudioPlayedRef\.current/);
  assert.match(appSource, /const terminalRunEvent = isTerminalRunEvent\(eventName, rawPayload\)/);
  assert.match(appSource, /if \(terminalRunEvent\)[\s\S]{0,420}syncAndSpeakLatestAssistant\(conversationId\)/);
  assert.match(appSource, /mode === 'voice_tag'[\s\S]{0,320}else[\s\S]{0,220}stripVoiceTagMarkup\(text\)/);
  assert.match(appSource, /const shouldPlayVoiceArtifact = settingsRef\.current\.eventVoiceMode === 'voice_tag'/);
  assert.match(appSource, /if \(shouldPlayVoiceArtifact && audioUrl/);
  assert.match(appSource, /if \(shouldPlayVoiceArtifact && audioData\)/);
});
