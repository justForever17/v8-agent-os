const assert = require('node:assert/strict');
const test = require('node:test');
const { buildTrayMenuModel } = require('../lib/tray-menu.cjs');

test('tray menu starts desktop pet when it is not running', () => {
  const model = buildTrayMenuModel({ desktopPetState: 'stopped', desktopPetProcessRunning: false });
  const ids = model.map((item) => item.id || item.type);
  assert.ok(ids.includes('open-web'));
  assert.ok(ids.includes('open-admin'));
  assert.equal(model.find((item) => item.id === 'desktop-pet-status')?.label, '桌宠：已关闭');
  assert.ok(ids.includes('start-desktop-pet'));
  assert.ok(ids.includes('check-update'));
  assert.ok(ids.includes('quit-v8os'));
  assert.equal(ids.includes('stop-desktop-pet'), false);
});

test('tray menu projects update checks without exposing raw release data', () => {
  const checking = buildTrayMenuModel({ updateStatus: { state: 'checking' } });
  assert.equal(checking.find((item) => item.id === 'update-status')?.enabled, false);
  assert.match(checking.find((item) => item.id === 'update-status')?.label, /Checking for updates/);

  const available = buildTrayMenuModel({
    updateStatus: { state: 'available', version: '2026.08.10.1', releaseUrl: 'ignored' },
  });
  assert.equal(
    available.find((item) => item.id === 'open-update-release')?.label,
    '新版本 2026.08.10.1 / Update available',
  );
  assert.doesNotMatch(JSON.stringify(available), /releaseUrl|github\.com/);

  const failed = buildTrayMenuModel({ updateStatus: { state: 'error', errorCode: 'network_unavailable' } });
  assert.ok(failed.some((item) => item.id === 'check-update'));
  assert.doesNotMatch(JSON.stringify(failed), /network_unavailable/);
});

test('tray menu stops desktop pet when it is running', () => {
  const model = buildTrayMenuModel({ desktopPetState: 'connected', desktopPetProcessRunning: true });
  const ids = model.map((item) => item.id || item.type);
  assert.equal(model.find((item) => item.id === 'desktop-pet-status')?.label, '桌宠：已连接');
  assert.ok(ids.includes('stop-desktop-pet'));
  assert.equal(ids.includes('start-desktop-pet'), false);
});

test('tray menu disables lifecycle actions during transitions', () => {
  for (const state of ['starting', 'stopping']) {
    const action = buildTrayMenuModel({
      desktopPetState: state,
      desktopPetProcessRunning: state === 'stopping',
    }).find((item) => item.id === (state === 'starting' ? 'start-desktop-pet' : 'stop-desktop-pet'));
    assert.equal(action?.enabled, false);
  }
});

test('tray menu exposes no Linux desktop pet start action when the runtime is unavailable', () => {
  const model = buildTrayMenuModel({
    desktopPetState: 'stopped',
    desktopPetProcessRunning: false,
    desktopPetAvailability: {
      available: false,
      status: 'unavailable',
      reasonCode: 'linux_desktop_pet_input_passthrough_unreliable',
    },
  });
  const ids = model.map((item) => item.id || item.type);
  assert.equal(
    model.find((item) => item.id === 'desktop-pet-status')?.label,
    '桌宠：Linux 暂不可用 / Companion unavailable on Linux',
  );
  assert.equal(ids.includes('start-desktop-pet'), false);
  assert.equal(ids.includes('stop-desktop-pet'), false);
});

test('tray menu can stop a residual desktop pet even when Linux runtime is unavailable', () => {
  const model = buildTrayMenuModel({
    desktopPetState: 'starting',
    desktopPetProcessRunning: true,
    desktopPetAvailability: {
      available: false,
      status: 'unavailable',
      reasonCode: 'linux_desktop_pet_input_passthrough_unreliable',
    },
  });
  const ids = model.map((item) => item.id || item.type);
  assert.ok(ids.includes('stop-desktop-pet'));
  assert.equal(ids.includes('start-desktop-pet'), false);
  assert.equal(model.find((item) => item.id === 'stop-desktop-pet')?.enabled, true);
});

test('tray menu does not label an unconfirmed capability as Linux-specific', () => {
  const model = buildTrayMenuModel({
    desktopPetState: 'stopped',
    desktopPetProcessRunning: false,
    desktopPetAvailability: {
      available: false,
      status: 'unavailable',
      reasonCode: 'desktop_pet_availability_unconfirmed',
    },
  });
  assert.equal(
    model.find((item) => item.id === 'desktop-pet-status')?.label,
    '桌宠：运行状态不可用 / Companion runtime unavailable',
  );
});
