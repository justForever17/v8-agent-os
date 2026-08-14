'use strict';

const DEFAULT_HANDOFF_GRACE_MS = 5_000;
const DEFAULT_HANDOFF_POLL_MS = 200;

function hasServiceEvidence(status) {
  if (!status || status.state === 'external_port_in_use') return false;
  // `pidAlive` and `portOpen` can both describe an unverified/stale process.
  // The process manager must first prove ownership; an arbitrary listener is
  // never evidence that this launch attempt succeeded.
  return status.managed === true && status.pidAlive === true;
}

/**
 * A launcher can exit while its managed server is still binding its socket.
 * Reconcile that short handoff window without turning a dead service into a
 * 120-second readiness timeout. The caller remains responsible for identity
 * verification and user-facing error projection.
 */
async function waitForServiceHandoff(serviceIds, options = {}) {
  const ids = [...new Set((serviceIds || []).map(String).filter(Boolean))];
  const statusProvider = options.statusProvider;
  if (typeof statusProvider !== 'function') throw new TypeError('statusProvider is required');
  const now = options.now || (() => Date.now());
  const sleep = options.sleep || ((delayMs) => new Promise((resolve) => setTimeout(resolve, delayMs)));
  const graceMs = Math.max(0, Number(options.graceMs ?? DEFAULT_HANDOFF_GRACE_MS));
  const pollMs = Math.max(1, Number(options.pollMs ?? DEFAULT_HANDOFF_POLL_MS));
  const deadline = now() + graceMs;
  let statuses = [];

  while (true) {
    statuses = await statusProvider(ids);
    const byId = new Map((Array.isArray(statuses) ? statuses : []).map((status) => [String(status?.id || ''), status]));
    if (ids.every((id) => hasServiceEvidence(byId.get(id)))) {
      return { ok: true, statuses: Array.isArray(statuses) ? statuses : [] };
    }
    if (now() >= deadline) {
      return { ok: false, statuses: Array.isArray(statuses) ? statuses : [] };
    }
    await sleep(Math.min(pollMs, Math.max(1, deadline - now())));
  }
}

module.exports = {
  DEFAULT_HANDOFF_GRACE_MS,
  DEFAULT_HANDOFF_POLL_MS,
  hasServiceEvidence,
  waitForServiceHandoff,
};
