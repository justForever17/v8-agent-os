const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const { createCanonicalConfigWatcher } = require('../lib/canonical-config-watcher.cjs');

test('canonical config watcher debounces atomic config replacements and ignores unrelated files', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8os-pet-config-watch-'));
  const events = [];
  let changeHandler = null;
  let closed = false;
  const watcher = createCanonicalConfigWatcher({
    filePath: path.join(root, 'config.json'),
    debounceMs: 20,
    onChange: (event) => events.push(event),
    watchDirectory: (_directory, _options, handler) => {
      changeHandler = handler;
      return {
        close() { closed = true; },
        on() {},
      };
    },
  });
  try {
    watcher.start();
    changeHandler('change', 'unrelated.json');
    changeHandler('rename', 'config.json');
    changeHandler('change', 'config.json');
    await new Promise((resolve) => setTimeout(resolve, 55));
    assert.equal(events.length, 1);
    assert.equal(events[0].domain, 'desktop-pet');
  } finally {
    watcher.stop();
    fs.rmSync(root, { recursive: true, force: true });
  }
  assert.equal(closed, true);
});
