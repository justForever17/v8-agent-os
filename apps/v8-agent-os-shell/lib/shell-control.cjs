const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const CONTROL_VERSION = 2;
const SESSION_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{5,180}$/;
function runtimeRootPath() {
  const stateRoot = String(process.env.V8_AGENT_OS_HOME || '').trim();
  return path.join(stateRoot ? path.resolve(stateRoot) : path.join(os.homedir(), '.v8-agent-os'), 'runtime');
}

function shellControlDescriptorPath(runtimeRoot = runtimeRootPath()) {
  return path.join(runtimeRoot, 'shell-control.json');
}

function isValidSessionId(value) {
  return SESSION_ID_PATTERN.test(String(value || '').trim());
}

function writeDescriptorAtomic(filePath, descriptor) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const temporaryPath = `${filePath}.${process.pid}.${crypto.randomBytes(6).toString('hex')}.tmp`;
  fs.writeFileSync(temporaryPath, `${JSON.stringify(descriptor, null, 2)}\n`, { encoding: 'utf8', mode: 0o600 });
  try {
    fs.renameSync(temporaryPath, filePath);
  } catch {
    fs.rmSync(filePath, { force: true });
    fs.renameSync(temporaryPath, filePath);
  }
}

function removeOwnedDescriptor(filePath, descriptor) {
  try {
    const current = JSON.parse(fs.readFileSync(filePath, 'utf8'));
    if (current?.pid === descriptor.pid && current?.token === descriptor.token) {
      fs.rmSync(filePath, { force: true });
    }
  } catch {
    // Missing or replaced descriptors belong to another Shell instance.
  }
}

function createShellState(options = {}) {
  const runtimeRoot = options.runtimeRoot || runtimeRootPath();
  const descriptorPath = options.descriptorPath || shellControlDescriptorPath(runtimeRoot);
  let descriptor = null;
  let previousActiveSessionId = null;

  return {
    async start() {
      if (descriptor) return descriptor;
      fs.mkdirSync(runtimeRoot, { recursive: true });
      try {
        const previousDescriptor = JSON.parse(fs.readFileSync(descriptorPath, 'utf8'));
        if (isValidSessionId(previousDescriptor?.activeSessionId)) {
          previousActiveSessionId = String(previousDescriptor.activeSessionId).trim();
        }
      } catch {
        previousActiveSessionId = null;
      }
      const token = crypto.randomBytes(32).toString('hex');

      descriptor = {
        version: CONTROL_VERSION,
        pid: process.pid,
        token,
        activeSessionId: previousActiveSessionId,
        createdAt: new Date().toISOString(),
        surfaceReady: false,
        surfaceKind: null,
        surfaceReadyAt: null,
        ...(options.packaged === true ? {
          packaged: true,
          runtimeKind: 'shell',
          executablePath: path.resolve(String(options.executablePath || process.execPath)),
          repoRoot: path.resolve(String(options.repoRoot || process.env.V8_REPO_ROOT || '')),
          softwareRendering: options.softwareRendering === true,
        } : {}),
      };
      writeDescriptorAtomic(descriptorPath, descriptor);
      return { ...descriptor, token: undefined, previousActiveSessionId };
    },
    setActiveSession(sessionId) {
      if (!descriptor) return false;
      descriptor.activeSessionId = isValidSessionId(sessionId) ? String(sessionId).trim() : null;
      writeDescriptorAtomic(descriptorPath, descriptor);
      return true;
    },
    setRuntimeStatus(status) {
      if (!descriptor || !status || typeof status !== 'object') return false;
      descriptor.status = {
        desktopPetState: String(status.desktopPetState || 'stopped'),
        desktopPetProcessRunning: Boolean(status.desktopPetProcessRunning),
        desktopPetAvailable: status.desktopPetAvailable !== false,
        desktopPetUnavailableReasonCode: status.desktopPetUnavailableReasonCode || null,
        controlConnected: Boolean(status.controlConnected),
        desktopPetActiveSessionId: isValidSessionId(status.desktopPetActiveSessionId)
          ? String(status.desktopPetActiveSessionId).trim()
          : null,
        updatedAt: new Date().toISOString(),
      };
      writeDescriptorAtomic(descriptorPath, descriptor);
      return true;
    },
    setSurfaceStatus(status) {
      if (!descriptor || !status || typeof status !== 'object') return false;
      const allowedSurfaceKinds = new Set(['web', 'admin', 'admin-login']);
      const surfaceReady = Boolean(status.surfaceReady) && allowedSurfaceKinds.has(status.surfaceKind);
      descriptor.surfaceReady = surfaceReady;
      descriptor.surfaceKind = surfaceReady ? status.surfaceKind : null;
      descriptor.surfaceReadyAt = surfaceReady ? new Date().toISOString() : null;
      writeDescriptorAtomic(descriptorPath, descriptor);
      return true;
    },
    descriptorPath,
    async stop() {
      const ownedDescriptor = descriptor;
      descriptor = null;
      if (ownedDescriptor) {
        removeOwnedDescriptor(descriptorPath, ownedDescriptor);
      }
    },
  };
}

module.exports = { CONTROL_VERSION, createShellState, isValidSessionId, runtimeRootPath, shellControlDescriptorPath };
