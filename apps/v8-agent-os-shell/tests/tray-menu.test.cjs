const assert = require('node:assert/strict');
const test = require('node:test');
const { buildTrayMenuModel } = require('../lib/tray-menu.cjs');

test('tray menu starts desktop pet when it is not running', () => {
  const ids = buildTrayMenuModel({ desktopPetRunning: false }).map((item) => item.id || item.type);
  assert.ok(ids.includes('open-web'));
  assert.ok(ids.includes('open-admin'));
  assert.ok(ids.includes('start-desktop-pet'));
  assert.ok(ids.includes('quit-v8os'));
  assert.equal(ids.includes('stop-desktop-pet'), false);
});

test('tray menu stops desktop pet when it is running', () => {
  const ids = buildTrayMenuModel({ desktopPetRunning: true }).map((item) => item.id || item.type);
  assert.ok(ids.includes('stop-desktop-pet'));
  assert.equal(ids.includes('start-desktop-pet'), false);
});
