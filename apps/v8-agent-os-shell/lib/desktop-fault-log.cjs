const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const MAX_FAULT_LOG_BYTES = 128 * 1024;
const EVENTS = new Set(['gpu-process-exited', 'renderer-process-exited', 'surface-recovery']);
const REASONS = new Set(['clean-exit', 'abnormal-exit', 'killed', 'crashed', 'oom', 'launch-failed', 'integrity-failure', 'memory-eviction']);
const STAGES = new Set(['observed', 'scheduled', 'exhausted', 'recovered', 'relaunch-requested', 'relaunch-unhandled']);
const SURFACES = new Set(['web', 'admin', 'startup', 'desktop-pet']);

function desktopFaultLogPath(environment = process.env, homeDirectory = os.homedir()) {
  // Same state root and log directory as CLI paths.mjs; no service/import cycle.
  const stateRoot = path.resolve(environment.V8_AGENT_OS_HOME || path.join(homeDirectory, '.v8-agent-os'));
  const component = environment.V8OS_DESKTOP_RUNTIME_MODE === 'desktop-pet' ? 'desktop-pet' : 'shell';
  return path.join(stateRoot, 'logs', 'cli', `${component}-faults.jsonl`);
}

function createDesktopFaultRecorder(options = {}) {
  const logPath = options.logPath || desktopFaultLogPath();
  const requestedLimit = Number(options.maxBytes);
  const maxBytes = Number.isFinite(requestedLimit)
    ? Math.max(1024, Math.min(MAX_FAULT_LOG_BYTES, requestedLimit)) : MAX_FAULT_LOG_BYTES;
  return (event, details = {}) => {
    try {
      if (!EVENTS.has(event)) return false;
      // Do not serialize Electron details, console arguments, URL or page data.
      const record = {
        timestamp: new Date().toISOString(),
        pid: process.pid,
        event,
        stage: STAGES.has(details.stage) ? details.stage : 'observed',
        surface: SURFACES.has(details.surface) ? details.surface : 'unknown',
        reason: REASONS.has(details.reason) ? details.reason : 'unknown',
        exitCode: Number.isSafeInteger(details.exitCode) ? details.exitCode : null,
      };
      const line = `${JSON.stringify(record)}\n`;
      fs.mkdirSync(path.dirname(logPath), { recursive: true, mode: 0o700 });
      const size = fs.existsSync(logPath) ? fs.statSync(logPath).size : 0;
      if (size + Buffer.byteLength(line) > maxBytes) {
        fs.rmSync(`${logPath}.1`, { force: true });
        if (size > maxBytes) fs.rmSync(logPath);
        else fs.renameSync(logPath, `${logPath}.1`);
      }
      fs.appendFileSync(logPath, line, { encoding: 'utf8', mode: 0o600 });
      return true;
    } catch {
      // Fault reporting must not cause another application failure.
      return false;
    }
  };
}

const recordDesktopFault = createDesktopFaultRecorder();
module.exports = { MAX_FAULT_LOG_BYTES, desktopFaultLogPath, createDesktopFaultRecorder, recordDesktopFault };
