const assert = require('node:assert/strict');
const test = require('node:test');

const { isExpectedNavigationAbort, loadUrlSafely } = require('../lib/navigation-load.cjs');

test('navigation aborts caused by a newer load are expected', async () => {
  assert.equal(isExpectedNavigationAbort({ code: 'ERR_ABORTED' }), true);
  assert.equal(isExpectedNavigationAbort({ errno: -3 }), true);
  assert.equal(isExpectedNavigationAbort(new Error('ERR_ABORTED (-3) loading URL')), true);
  let unexpected = 0;
  const loaded = await loadUrlSafely(
    async () => { throw Object.assign(new Error('aborted'), { code: 'ERR_ABORTED' }); },
    () => { unexpected += 1; },
  );
  assert.equal(loaded, false);
  assert.equal(unexpected, 0);
});

test('unexpected navigation failures are reported without rejecting callers', async () => {
  const failure = Object.assign(new Error('connection refused'), { code: 'ERR_CONNECTION_REFUSED' });
  let reported;
  const loaded = await loadUrlSafely(
    async () => { throw failure; },
    (error) => { reported = error; },
  );
  assert.equal(loaded, false);
  assert.equal(reported, failure);
});

test('successful navigation resolves true', async () => {
  assert.equal(await loadUrlSafely(async () => undefined), true);
});
