const crypto = require('node:crypto');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');

const CONTROL_VERSION = 1;
const MAX_MESSAGE_BYTES = 64 * 1024;
const SESSION_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{5,180}$/;
const PET_STATES = new Set(['waiting_v8os', 'connected', 'stopping', 'error']);
const PET_TO_SHELL_TYPES = new Set(['pet-status', 'open-settings', 'open-session', 'shutdown-ready']);
const SHELL_TO_PET_TYPES = new Set(['active-session', 'shutdown']);

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

function validatePetMessage(message) {
  if (!message || typeof message !== 'object' || !PET_TO_SHELL_TYPES.has(message.type)) return false;
  if (message.type === 'pet-status') {
    if (!PET_STATES.has(message.state)) return false;
    return message.activeSessionId == null || message.activeSessionId === '' || isValidSessionId(message.activeSessionId);
  }
  if (message.type === 'open-session') return isValidSessionId(message.sessionId);
  if (message.type === 'shutdown-ready') return typeof message.requestId === 'string' && message.requestId.length <= 120;
  return true;
}

function validateShellMessage(message) {
  if (!message || typeof message !== 'object' || !SHELL_TO_PET_TYPES.has(message.type)) return false;
  if (message.type === 'active-session') {
    return message.sessionId == null || message.sessionId === '' || isValidSessionId(message.sessionId);
  }
  return typeof message.requestId === 'string' && message.requestId.length <= 120;
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

function createShellControlServer(options = {}) {
  const runtimeRoot = options.runtimeRoot || runtimeRootPath();
  const descriptorPath = options.descriptorPath || shellControlDescriptorPath(runtimeRoot);
  const onAuthenticated = options.onAuthenticated || (() => undefined);
  const onDisconnect = options.onDisconnect || (() => undefined);
  const onMessage = options.onMessage || (() => undefined);
  const clients = new Set();
  let server = null;
  let descriptor = null;
  let previousActiveSessionId = null;

  const sendLine = (socket, message) => {
    if (!socket || socket.destroyed) return false;
    const line = `${JSON.stringify(message)}\n`;
    if (Buffer.byteLength(line) > MAX_MESSAGE_BYTES) return false;
    socket.write(line);
    return true;
  };

  const handleConnection = (socket) => {
    socket.setNoDelay(true);
    let authenticated = false;
    let buffer = '';

    const reject = () => {
      try { socket.destroy(); } catch {}
    };

    socket.on('data', (chunk) => {
      buffer += chunk.toString('utf8');
      if (Buffer.byteLength(buffer) > MAX_MESSAGE_BYTES && !buffer.includes('\n')) {
        reject();
        return;
      }

      while (buffer.includes('\n')) {
        const newlineIndex = buffer.indexOf('\n');
        const rawLine = buffer.slice(0, newlineIndex);
        buffer = buffer.slice(newlineIndex + 1);
        if (!rawLine.trim() || Buffer.byteLength(rawLine) > MAX_MESSAGE_BYTES) {
          reject();
          return;
        }
        let message;
        try {
          message = JSON.parse(rawLine);
        } catch {
          reject();
          return;
        }

        if (!authenticated) {
          if (
            message?.type !== 'hello'
            || message?.version !== CONTROL_VERSION
            || message?.role !== 'desktop-pet'
            || message?.token !== descriptor?.token
          ) {
            reject();
            return;
          }
          for (const existing of clients) {
            if (existing !== socket) existing.destroy();
          }
          authenticated = true;
          clients.add(socket);
          sendLine(socket, { type: 'hello-ack', version: CONTROL_VERSION });
          onAuthenticated();
          continue;
        }

        if (!validatePetMessage(message)) {
          reject();
          return;
        }
        onMessage(message);
      }
    });

    socket.on('close', () => {
      if (!authenticated) return;
      clients.delete(socket);
      if (clients.size === 0) onDisconnect();
    });
    socket.on('error', () => undefined);
  };

  return {
    async start() {
      if (server) return descriptor;
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
      const endpoint = process.platform === 'win32'
        ? `\\\\.\\pipe\\v8os-shell-${process.pid}-${token.slice(0, 12)}`
        : path.join(runtimeRoot, `shell-control-${process.pid}.sock`);
      if (process.platform !== 'win32') fs.rmSync(endpoint, { force: true });

      descriptor = {
        version: CONTROL_VERSION,
        endpoint,
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
        } : {}),
      };
      server = net.createServer(handleConnection);
      await new Promise((resolve, reject) => {
        const handleError = (error) => {
          server?.off('listening', handleListening);
          reject(error);
        };
        const handleListening = () => {
          server?.off('error', handleError);
          resolve();
        };
        server.once('error', handleError);
        server.once('listening', handleListening);
        server.listen(endpoint);
      });
      writeDescriptorAtomic(descriptorPath, descriptor);
      return { ...descriptor, token: undefined, previousActiveSessionId };
    },
    send(type, payload = {}) {
      const message = { type, ...payload };
      if (!validateShellMessage(message)) return 0;
      let sent = 0;
      for (const client of clients) {
        if (sendLine(client, message)) sent += 1;
      }
      return sent;
    },
    hasAuthenticatedClient() {
      return clients.size > 0;
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
      for (const client of clients) client.destroy();
      clients.clear();
      const activeServer = server;
      const ownedDescriptor = descriptor;
      server = null;
      descriptor = null;
      if (ownedDescriptor) {
        removeOwnedDescriptor(descriptorPath, ownedDescriptor);
        if (process.platform !== 'win32') fs.rmSync(ownedDescriptor.endpoint, { force: true });
      }
      if (activeServer) {
        await new Promise((resolve) => activeServer.close(() => resolve()));
      }
    },
  };
}

module.exports = {
  CONTROL_VERSION,
  MAX_MESSAGE_BYTES,
  PET_TO_SHELL_TYPES,
  SHELL_TO_PET_TYPES,
  createShellControlServer,
  isValidSessionId,
  runtimeRootPath,
  shellControlDescriptorPath,
  validatePetMessage,
  validateShellMessage,
};
