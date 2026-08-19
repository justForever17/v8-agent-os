const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const petRoot = path.resolve(__dirname, '..', '..', 'v8-agent-os-desktop-pet');
const {
  initialSafeShape,
  normalizeInteractionRegions,
} = require(path.join(petRoot, 'lib', 'interaction-region-policy.cjs'));

test('Windows desktop pet starts with a one-pixel nonblocking input shape', () => {
  assert.deepEqual(initialSafeShape({ width: 1920, height: 1080 }), [
    { x: 1919, y: 1079, width: 1, height: 1 },
  ]);
});

test('desktop pet interaction regions are padded, clamped, and bounded', () => {
  assert.deepEqual(
    normalizeInteractionRegions([
      { x: -10, y: 20, width: 192, height: 192 },
      { x: 1500, y: 200, width: 320, height: 640 },
    ], { width: 1920, height: 1080 }),
    [
      { x: 0, y: 0, width: 206, height: 236 },
      { x: 1476, y: 176, width: 368, height: 688 },
    ],
  );
  assert.deepEqual(
    normalizeInteractionRegions([{ x: 0, y: 0, width: 1920, height: 1080 }], { width: 1920, height: 1080 }),
    [],
  );
  assert.deepEqual(
    normalizeInteractionRegions([{ x: Number.NaN, y: 0, width: 20, height: 20 }], { width: 1920, height: 1080 }),
    [],
  );
});

test('renderer reports only the visible pet and open menu while Windows main owns the OS shape', () => {
  const main = fs.readFileSync(path.join(petRoot, 'electron', 'main.cjs'), 'utf8');
  const preload = fs.readFileSync(path.join(petRoot, 'electron', 'preload.cjs'), 'utf8');
  const renderer = fs.readFileSync(path.join(petRoot, 'src', 'components', 'CyberPet.tsx'), 'utf8');

  assert.match(main, /mainWindow\.setShape\(normalized\)/);
  assert.match(main, /if \(normalized\.length < 1\) return/);
  assert.match(preload, /ipcRenderer\.send\('v8-desktop:set-interaction-regions'/);
  assert.match(renderer, /data-menu-panel="true"\]\[data-motion-state="open"/);
  assert.match(renderer, /getBoundingClientRect\(\)/);
  assert.match(renderer, /window\.v8CyberCore\?\.platform !== 'win32'/);
});
