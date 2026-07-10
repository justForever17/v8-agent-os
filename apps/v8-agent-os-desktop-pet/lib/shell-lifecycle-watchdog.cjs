const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

function runtimeRootPath() {
  const stateRoot = process.env.V8_AGENT_OS_HOME || path.join(os.homedir(), '.v8-agent-os');
  return path.join(stateRoot, 'runtime');
}

function shellControlDescriptorPath(runtimeRoot = runtimeRootPath()) {
  return path.join(runtimeRoot, 'shell-control.json');
}

function shellRestartLeasePath(runtimeRoot = runtimeRootPath()) {
  return path.join(runtimeRoot, 'shell-restart.json');
}

function readJson(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
  } catch {
    return null;
  }
}

function readShellDescriptor(filePath = shellControlDescriptorPath()) {
  const descriptor = readJson(filePath);
  const pid = Number(descriptor?.pid);
  if (!Number.isInteger(pid) || pid <= 0) return null;
  return { ...descriptor, pid };
}

function isProcessAlive(pid) {
  const numeric = Number(pid);
  if (!Number.isInteger(numeric) || numeric <= 0) return false;
  try {
    process.kill(numeric, 0);
    return true;
  } catch (error) {
    return error?.code === 'EPERM';
  }
}

function isRestartLeaseActive(filePath = shellRestartLeasePath(), now = Date.now()) {
  const lease = readJson(filePath);
  const ownerPid = Number(lease?.ownerPid);
  return lease?.version === 1
    && lease?.reason === 'preview_rebuild'
    && Number.isFinite(Number(lease?.expiresAt))
    && Number(lease.expiresAt) > now
    && isProcessAlive(ownerPid);
}

function createShellLifecycleWatchdog(options = {}) {
  const descriptorPath = options.descriptorPath || shellControlDescriptorPath();
  const restartLeasePath = options.restartLeasePath || shellRestartLeasePath();
  const graceMs = Math.max(100, Number(options.graceMs) || 1800);
  const pollIntervalMs = Math.max(50, Number(options.pollIntervalMs) || 500);
  const now = options.now || Date.now;
  const readDescriptor = options.readDescriptor || (() => readShellDescriptor(descriptorPath));
  const processAlive = options.isProcessAlive || isProcessAlive;
  const restartLeaseActive = options.isRestartLeaseActive || (() => isRestartLeaseActive(restartLeasePath, now()));
  const isControlConnected = options.isControlConnected || (() => false);
  const onShellUnavailable = options.onShellUnavailable || (() => undefined);
  let disconnectedAt = null;
  let timer = null;
  let stopped = false;
  let triggered = false;

  const clearTimer = () => {
    if (timer) clearTimeout(timer);
    timer = null;
  };

  const schedule = (delay = pollIntervalMs) => {
    if (stopped || triggered || timer) return;
    timer = setTimeout(() => {
      timer = null;
      check();
    }, delay);
    timer.unref?.();
  };

  const check = () => {
    if (stopped || triggered) return;
    if (isControlConnected()) {
      disconnectedAt = null;
      clearTimer();
      return;
    }
    if (disconnectedAt == null) disconnectedAt = now();
    if (restartLeaseActive()) {
      schedule();
      return;
    }
    const descriptor = readDescriptor();
    if (descriptor && processAlive(descriptor.pid)) {
      schedule();
      return;
    }
    if (now() - disconnectedAt < graceMs) {
      schedule(Math.min(pollIntervalMs, graceMs));
      return;
    }
    triggered = true;
    clearTimer();
    onShellUnavailable({
      reason: descriptor ? 'shell_process_exited' : 'shell_descriptor_missing',
      shellPid: descriptor?.pid || null,
    });
  };

  return {
    markConnected() {
      disconnectedAt = null;
      triggered = false;
      clearTimer();
    },
    markDisconnected() {
      if (stopped || triggered) return;
      if (disconnectedAt == null) disconnectedAt = now();
      schedule(0);
    },
    check,
    stop() {
      stopped = true;
      clearTimer();
    },
  };
}

module.exports = {
  createShellLifecycleWatchdog,
  isProcessAlive,
  isRestartLeaseActive,
  readShellDescriptor,
  runtimeRootPath,
  shellControlDescriptorPath,
  shellRestartLeasePath,
};
