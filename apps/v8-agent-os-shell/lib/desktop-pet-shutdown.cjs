const crypto = require('node:crypto');

function createDesktopPetShutdownCoordinator() {
  const pending = new Map();

  return {
    request(sendShutdown, timeoutMs = 1500) {
      const requestId = crypto.randomUUID();
      const sent = sendShutdown(requestId);
      if (!sent) return Promise.resolve({ acked: false, reason: 'control_unavailable', requestId });
      return new Promise((resolve) => {
        const timer = setTimeout(() => {
          pending.delete(requestId);
          resolve({ acked: false, reason: 'shutdown_ack_timeout', requestId });
        }, timeoutMs);
        pending.set(requestId, {
          timer,
          resolve,
        });
      });
    },
    acknowledge(requestId) {
      const key = String(requestId || '');
      const entry = pending.get(key);
      if (!entry) return false;
      pending.delete(key);
      clearTimeout(entry.timer);
      entry.resolve({ acked: true, reason: 'renderer_ready', requestId: key });
      return true;
    },
    cancelAll() {
      for (const [requestId, entry] of pending.entries()) {
        pending.delete(requestId);
        clearTimeout(entry.timer);
        entry.resolve({ acked: false, reason: 'coordinator_cancelled', requestId });
      }
    },
  };
}

module.exports = { createDesktopPetShutdownCoordinator };
