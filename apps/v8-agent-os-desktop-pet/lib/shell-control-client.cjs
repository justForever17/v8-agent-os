const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');

const CONTROL_VERSION = 1;
const MAX_MESSAGE_BYTES = 64 * 1024;
const PET_TO_SHELL_TYPES = new Set(['pet-status', 'open-settings', 'open-session', 'shutdown-ready']);
const SHELL_TO_PET_TYPES = new Set(['active-session', 'shutdown']);
const SESSION_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{5,180}$/;

function defaultDescriptorPath() {
  return path.join(os.homedir(), '.v8-agent-os', 'runtime', 'shell-control.json');
}

function readDescriptor(filePath) {
  try {
    const descriptor = JSON.parse(fs.readFileSync(filePath, 'utf8'));
    if (
      descriptor?.version !== CONTROL_VERSION
      || typeof descriptor?.endpoint !== 'string'
      || !descriptor.endpoint
      || typeof descriptor?.token !== 'string'
      || !/^[a-f0-9]{64}$/i.test(descriptor.token)
      || !Number.isInteger(descriptor?.pid)
    ) {
      return null;
    }
    return descriptor;
  } catch {
    return null;
  }
}

function isValidSessionId(value) {
  return SESSION_ID_PATTERN.test(String(value || '').trim());
}

function validateOutgoingMessage(message) {
  if (!message || typeof message !== 'object' || !PET_TO_SHELL_TYPES.has(message.type)) return false;
  if (message.type === 'pet-status') {
    return new Set(['waiting_v8os', 'connected', 'stopping', 'error']).has(message.state)
      && (message.activeSessionId == null || message.activeSessionId === '' || isValidSessionId(message.activeSessionId));
  }
  if (message.type === 'open-session') return isValidSessionId(message.sessionId);
  if (message.type === 'shutdown-ready') return typeof message.requestId === 'string' && message.requestId.length <= 120;
  return true;
}

function validateIncomingMessage(message) {
  if (!message || typeof message !== 'object' || !SHELL_TO_PET_TYPES.has(message.type)) return false;
  if (message.type === 'active-session') {
    return message.sessionId == null || message.sessionId === '' || isValidSessionId(message.sessionId);
  }
  return typeof message.requestId === 'string' && message.requestId.length <= 120;
}

function createShellControlClient(options = {}) {
  const descriptorPath = options.descriptorPath || defaultDescriptorPath();
  const pollIntervalMs = Math.max(100, Number(options.pollIntervalMs) || 500);
  const onConnected = options.onConnected || (() => undefined);
  const onDisconnected = options.onDisconnected || (() => undefined);
  const onMessage = options.onMessage || (() => undefined);
  let socket = null;
  let authenticated = false;
  let connecting = false;
  let activeFingerprint = '';
  let pollTimer = null;
  let stopped = false;

  const destroySocket = () => {
    const current = socket;
    socket = null;
    authenticated = false;
    connecting = false;
    activeFingerprint = '';
    if (current && !current.destroyed) current.destroy();
  };

  const connect = (descriptor) => {
    connecting = true;
    activeFingerprint = `${descriptor.pid}:${descriptor.endpoint}:${descriptor.token}`;
    const nextSocket = net.createConnection(descriptor.endpoint);
    socket = nextSocket;
    let buffer = '';

    nextSocket.setNoDelay(true);
    nextSocket.on('connect', () => {
      const hello = {
        type: 'hello',
        version: CONTROL_VERSION,
        role: 'desktop-pet',
        token: descriptor.token,
      };
      nextSocket.write(`${JSON.stringify(hello)}\n`);
    });
    nextSocket.on('data', (chunk) => {
      buffer += chunk.toString('utf8');
      if (Buffer.byteLength(buffer) > MAX_MESSAGE_BYTES && !buffer.includes('\n')) {
        nextSocket.destroy();
        return;
      }
      while (buffer.includes('\n')) {
        const newlineIndex = buffer.indexOf('\n');
        const rawLine = buffer.slice(0, newlineIndex);
        buffer = buffer.slice(newlineIndex + 1);
        if (!rawLine.trim() || Buffer.byteLength(rawLine) > MAX_MESSAGE_BYTES) {
          nextSocket.destroy();
          return;
        }
        let message;
        try {
          message = JSON.parse(rawLine);
        } catch {
          nextSocket.destroy();
          return;
        }
        if (!authenticated) {
          if (message?.type !== 'hello-ack' || message?.version !== CONTROL_VERSION) {
            nextSocket.destroy();
            return;
          }
          authenticated = true;
          connecting = false;
          onConnected();
          continue;
        }
        if (!validateIncomingMessage(message)) {
          nextSocket.destroy();
          return;
        }
        onMessage(message);
      }
    });
    nextSocket.on('error', () => undefined);
    nextSocket.on('close', () => {
      if (socket !== nextSocket) return;
      const wasAuthenticated = authenticated;
      socket = null;
      authenticated = false;
      connecting = false;
      activeFingerprint = '';
      if (wasAuthenticated) onDisconnected();
    });
  };

  const tick = () => {
    if (stopped) return;
    const descriptor = readDescriptor(descriptorPath);
    if (!descriptor) {
      if (socket) destroySocket();
      return;
    }
    const fingerprint = `${descriptor.pid}:${descriptor.endpoint}:${descriptor.token}`;
    if (socket && activeFingerprint === fingerprint && (authenticated || connecting)) return;
    if (socket) destroySocket();
    connect(descriptor);
  };

  return {
    start() {
      if (pollTimer) return;
      stopped = false;
      tick();
      pollTimer = setInterval(tick, pollIntervalMs);
      pollTimer.unref?.();
    },
    send(type, payload = {}) {
      const message = { type, ...payload };
      if (!authenticated || !socket || socket.destroyed || !validateOutgoingMessage(message)) return false;
      const line = `${JSON.stringify(message)}\n`;
      if (Buffer.byteLength(line) > MAX_MESSAGE_BYTES) return false;
      socket.write(line);
      return true;
    },
    isConnected() {
      return authenticated;
    },
    stop() {
      stopped = true;
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = null;
      destroySocket();
    },
    tick,
  };
}

module.exports = {
  CONTROL_VERSION,
  MAX_MESSAGE_BYTES,
  createShellControlClient,
  defaultDescriptorPath,
  readDescriptor,
  validateIncomingMessage,
  validateOutgoingMessage,
};
