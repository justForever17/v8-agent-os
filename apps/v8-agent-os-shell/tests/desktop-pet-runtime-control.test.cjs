const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const shellRoot = path.resolve(__dirname, '..');

test('Admin and tray runtime controls share the graceful desktop pet shutdown path', () => {
  const mainSource = fs.readFileSync(path.join(shellRoot, 'electron', 'main.cjs'), 'utf8');
  const preloadSource = fs.readFileSync(path.join(shellRoot, 'electron', 'preload.cjs'), 'utf8');

  assert.match(preloadSource, /getDesktopPetState/);
  assert.match(preloadSource, /setDesktopPetEnabled/);
  assert.match(preloadSource, /onDesktopPetStateChange/);
  assert.match(mainSource, /v8os-shell:set-desktop-pet-enabled/);
  assert.match(mainSource, /async function setDesktopPetEnabled[\s\S]*await stopDesktopPetGracefully\(\)/);
  assert.match(mainSource, /async function toggleDesktopPet[\s\S]*return setDesktopPetEnabled\(!shouldStop\)/);
  assert.match(mainSource, /shellDesktopPetAvailability/);
  assert.match(mainSource, /state: desktopPetPlatformAvailability\.available \? desktopPetState : 'unavailable'/);
  assert.match(mainSource, /reasonCode: desktopPetPlatformAvailability\.reasonCode \|\| null/);

  const gracefulStart = mainSource.indexOf('async function stopDesktopPetGracefully');
  const gracefulEnd = mainSource.indexOf('async function setDesktopPetEnabled', gracefulStart);
  const gracefulSource = mainSource.slice(gracefulStart, gracefulEnd);
  assert.ok(gracefulSource.indexOf('if (!result.acked)') < gracefulSource.indexOf("shellStop(['desktop-pet'])"));

  const shutdownStart = mainSource.indexOf('async function runManagedV8OSShutdown');
  const shutdownEnd = mainSource.indexOf('function shutdownServiceLabel', shutdownStart);
  const shutdownSource = mainSource.slice(shutdownStart, shutdownEnd);
  assert.ok(shutdownSource.indexOf('await stopDesktopPetGracefully()') < shutdownSource.indexOf('shellStop(coreIds, stopOptions)'));
  assert.match(shutdownSource, /stopVerifiedPortOwners: coreIds/);
  assert.match(shutdownSource, /waitForServicesStopped\(shellStatus, 10, coreIds\)/);
  const finalGuardIndex = shutdownSource.indexOf('if (finalBlockers.length > 0)');
  const removeRecordIndex = shutdownSource.lastIndexOf('await removeShellProcessRecord');
  const stopControlIndex = shutdownSource.lastIndexOf('await stopControl');
  const quitApplicationIndex = shutdownSource.lastIndexOf('quitApplication()');
  assert.ok(finalGuardIndex < removeRecordIndex);
  assert.ok(removeRecordIndex < stopControlIndex);
  assert.ok(stopControlIndex < quitApplicationIndex);

  const quitStart = mainSource.indexOf('async function quitV8OS');
  const quitEnd = mainSource.indexOf('function updateTrayMenu', quitStart);
  const quitSource = mainSource.slice(quitStart, quitEnd);
  assert.match(quitSource, /quitting = false/);
  assert.match(quitSource, /await showShutdownFailure\(failure\)/);
  assert.doesNotMatch(quitSource, /finally \{[\s\S]*app\.quit\(\)/);
  assert.match(mainSource, /app\.on\('before-quit',[\s\S]{0,180}if \(quitting\) return;[\s\S]{0,180}void quitV8OS\(\)/);
  assert.match(mainSource, /MANAGED_SHELL_SHUTDOWN_ARG = '--v8os-managed-shutdown'/);
  assert.match(mainSource, /MANAGED_SHELL_RESTART_ARG = '--v8os-managed-restart'/);
  assert.match(mainSource, /process\.argv\.includes\(MANAGED_SHELL_SHUTDOWN_ARG\)[\s\S]{0,160}process\.argv\.includes\(MANAGED_SHELL_RESTART_ARG\)[\s\S]{0,320}app\.exit\(0\)/);
  assert.match(mainSource, /app\.on\('second-instance',[\s\S]{0,220}argv\.includes\(MANAGED_SHELL_SHUTDOWN_ARG\)[\s\S]{0,120}void quitV8OS\(\)/);
  assert.match(mainSource, /app\.on\('second-instance',[\s\S]{0,420}argv\.includes\(MANAGED_SHELL_RESTART_ARG\)[\s\S]{0,120}void quitShellForRestart\(\)/);
  assert.match(mainSource, /async function quitShellForRestart[\s\S]{0,900}removeShellProcessRecord[\s\S]{0,900}shellControl\?\.stop\(\)[\s\S]{0,500}app\.quit\(\)/);
});

test('Linux desktop pet availability blocks starts but preserves residual shutdown', () => {
  const mainSource = fs.readFileSync(path.join(shellRoot, 'electron', 'main.cjs'), 'utf8');
  const toggleStart = mainSource.indexOf('async function setDesktopPetEnabled');
  const toggleEnd = mainSource.indexOf('async function toggleDesktopPet', toggleStart);
  const toggleSource = mainSource.slice(toggleStart, toggleEnd);

  assert.match(toggleSource, /if \(!enabled && shouldStop\)[\s\S]*await stopDesktopPetGracefully\(\)/);
  assert.match(toggleSource, /enabled && !shouldStop && desktopPetPlatformAvailability\.available/);
  assert.ok(toggleSource.indexOf('await stopDesktopPetGracefully()') < toggleSource.indexOf("shellStart(['desktop-pet']"));
  assert.match(mainSource, /item\.state === 'managed_running' \|\| item\.pidAlive === true/);
  assert.match(mainSource, /desktopPetProcessRunning: desktopPetProcessRunning \|\| Boolean\(shellControl\?\.hasAuthenticatedClient\(\)\)/);
  assert.match(mainSource, /desktopPetAvailability: desktopPetPlatformAvailability/);
});
