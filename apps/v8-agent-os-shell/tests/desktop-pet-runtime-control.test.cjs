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
});
