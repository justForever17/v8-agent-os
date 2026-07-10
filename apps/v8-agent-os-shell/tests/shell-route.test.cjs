const assert = require('node:assert/strict');
const test = require('node:test');

const { parseShellDeepLink } = require('../lib/shell-route.cjs');

test('desktop pet settings deep link is allowlisted without arbitrary routes', () => {
  assert.deepEqual(parseShellDeepLink('v8os://open/admin/desktop-pet'), {
    surface: 'admin',
    path: '/admin/desktop-pet',
  });
  assert.equal(parseShellDeepLink('v8os://open/admin/models'), null);
  assert.equal(parseShellDeepLink('https://example.com/admin/desktop-pet'), null);
  assert.equal(parseShellDeepLink('v8os://open/admin/desktop-pet?next=https://example.com'), null);
});
