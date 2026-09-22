// Native behavior acceptance. Run with Shell's Electron, an isolated state root,
// and a production companion build. No model/provider calls are made.
const { app, protocol, BrowserWindow } = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const evidenceRoot = process.env.V8_AGENT_OS_HOME;
if (!evidenceRoot) throw Error('isolated V8_AGENT_OS_HOME required');
fs.mkdirSync(evidenceRoot, { recursive: true });
const stage = value => fs.appendFileSync(path.join(evidenceRoot, 'native-stages.log'), `${value}\n`);
process.on('uncaughtException', error => { stage(error.stack); app.exit(1); });
process.on('unhandledRejection', error => { stage(String(error?.stack || error)); app.exit(1); });
const deadline = setTimeout(() => { stage('native test exceeded 45 seconds'); app.exit(1); }, 45000);
const { createCompanionWindow, registerStableRendererScheme } = require('../../../v8-agent-os-desktop-pet/electron/companion-window.cjs');
if (!process.env.V8_AGENT_OS_HOME) throw Error('isolated V8_AGENT_OS_HOME required');
app.setPath('userData', path.join(process.env.V8_AGENT_OS_HOME, 'electron'));
registerStableRendererScheme(protocol);
let host;
app.whenReady().then(async () => {
  try {
    stage('ready');
    host = createCompanionWindow();
    stage('host created');
    await host.start();
    stage('host started');
    const first = BrowserWindow.getAllWindows()[0];
    assert.ok(first);
    const contents = await first.webContents.executeJavaScript('document.body.innerText');
    assert.equal(typeof contents, 'string');
    const identity = host.status();
    assert.equal(identity.hostPid, process.pid);
    const stopped = await host.stop();
    stage(`first stop: ${JSON.stringify(stopped)}`);
    assert.equal(stopped.stopped, true);
    assert.equal(host.status().running, false);
    await host.start();
    stage('host restarted');
    const restarted = BrowserWindow.getAllWindows()[0];
    assert.notEqual(restarted.id, first.id);
    restarted.webContents.forcefullyCrashRenderer();
    const crashed = await host.stop();
    assert.equal(crashed.stopped, true);
    assert.equal(host.status().running, false);
    await host.dispose();
    clearTimeout(deadline);
    fs.writeFileSync(path.join(process.env.V8_AGENT_OS_HOME, 'native-result.json'), JSON.stringify({
      ok: true, hostPid: process.pid, restarted: true, rendererCrashContained: true,
      acknowledged: stopped.acked, crashShutdown: crashed.reason,
    }, null, 2));
    app.exit(0);
  } catch (error) {
    stage(error.stack);
    console.error(error);
    await host?.dispose();
    app.exit(1);
  }
});
app.on('window-all-closed', () => {});
