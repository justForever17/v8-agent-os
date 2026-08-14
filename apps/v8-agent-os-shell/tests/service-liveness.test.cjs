'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

const { hasServiceEvidence, waitForServiceHandoff } = require('../lib/service-liveness.cjs');

test('service handoff accepts a listener that appears during the bounded grace window', async () => {
  let now = 0;
  let calls = 0;
  const result = await waitForServiceHandoff(['admin'], {
    graceMs: 1_000,
    pollMs: 100,
    now: () => now,
    sleep: async (delay) => { now += delay; },
    statusProvider: async () => {
      calls += 1;
      return [{ id: 'admin', managed: calls >= 3, pidAlive: calls >= 3, portOpen: calls >= 3, state: calls >= 3 ? 'managed_running' : 'stopped' }];
    },
  });
  assert.equal(result.ok, true);
  assert.equal(calls, 3);
});

test('service handoff fails closed after the bounded grace window', async () => {
  let now = 0;
  const result = await waitForServiceHandoff(['admin'], {
    graceMs: 200,
    pollMs: 100,
    now: () => now,
    sleep: async (delay) => { now += delay; },
    statusProvider: async () => [{ id: 'admin', pidAlive: false, portOpen: false, state: 'stopped' }],
  });
  assert.equal(result.ok, false);
  assert.equal(hasServiceEvidence(result.statuses[0]), false);
});

test('an unrelated listener never satisfies the handoff contract', () => {
  assert.equal(hasServiceEvidence({ pidAlive: false, portOpen: true, state: 'external_port_in_use' }), false);
  assert.equal(hasServiceEvidence({ pidAlive: true, portOpen: true, state: 'managed_identity_unverified' }), false);
  assert.equal(hasServiceEvidence({ managed: true, pidAlive: true, portOpen: false, state: 'managed_running' }), true);
});
