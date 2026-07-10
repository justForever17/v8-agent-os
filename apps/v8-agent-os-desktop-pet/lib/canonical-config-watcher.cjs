const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

function canonicalConfigPath() {
  const stateRoot = process.env.V8_AGENT_OS_HOME || path.join(os.homedir(), '.v8-agent-os');
  return path.join(stateRoot, 'config.json');
}

function createCanonicalConfigWatcher(options = {}) {
  const filePath = options.filePath || canonicalConfigPath();
  const debounceMs = Math.max(50, Number(options.debounceMs) || 180);
  const onChange = options.onChange || (() => undefined);
  const watchDirectory = options.watchDirectory || fs.watch;
  let watcher = null;
  let debounceTimer = null;
  let stopped = false;

  const schedule = () => {
    if (stopped) return;
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      debounceTimer = null;
      onChange({ domain: 'desktop-pet', changedAt: Date.now() });
    }, debounceMs);
    debounceTimer.unref?.();
  };

  return {
    start() {
      if (watcher || stopped) return;
      const directory = path.dirname(filePath);
      const targetName = path.basename(filePath).toLowerCase();
      fs.mkdirSync(directory, { recursive: true });
      watcher = watchDirectory(directory, { persistent: false }, (_eventType, filename) => {
        const changedName = filename == null ? '' : String(filename).toLowerCase();
        if (!changedName || changedName === targetName) schedule();
      });
      watcher.on?.('error', () => undefined);
    },
    stop() {
      stopped = true;
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = null;
      watcher?.close?.();
      watcher = null;
    },
    schedule,
  };
}

module.exports = {
  canonicalConfigPath,
  createCanonicalConfigWatcher,
};
