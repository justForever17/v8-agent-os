const assert = require('node:assert/strict');
const test = require('node:test');

const { createDesktopPetShutdownCoordinator } = require('../lib/desktop-pet-shutdown.cjs');

test('desktop pet graceful shutdown resolves when the renderer acknowledges', async () => {
  const coordinator = createDesktopPetShutdownCoordinator();
  let requestId = '';
  const pending = coordinator.request((nextRequestId) => {
    requestId = nextRequestId;
    return true;
  }, 100);
  assert.equal(coordinator.acknowledge(requestId), true);
  const result = await pending;
  assert.equal(result.acked, true);
  assert.equal(result.reason, 'renderer_ready');
});

test('desktop pet graceful shutdown exposes the timeout fallback reason', async () => {
  const coordinator = createDesktopPetShutdownCoordinator();
  const result = await coordinator.request(() => true, 20);
  assert.equal(result.acked, false);
  assert.equal(result.reason, 'shutdown_ack_timeout');
});

test('coordinator cancellation does not masquerade as a renderer acknowledgement', async () => {
  const coordinator = createDesktopPetShutdownCoordinator();
  const pending = coordinator.request(() => true, 1000);
  coordinator.cancelAll();
  const result = await pending;
  assert.equal(result.acked, false);
  assert.equal(result.reason, 'coordinator_cancelled');
});
