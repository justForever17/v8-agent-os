import net from "node:net";
import { spawnSync } from "node:child_process";

function probePort(port, host, timeoutMs) {
  return new Promise((resolve) => {
    const socket = net.createConnection({ host, port });
    let settled = false;
    const done = (value) => {
      if (settled) return;
      settled = true;
      socket.destroy();
      resolve(value);
    };
    socket.setTimeout(timeoutMs);
    socket.once("connect", () => done(true));
    socket.once("timeout", () => done(false));
    socket.once("error", () => done(false));
  });
}

export async function isPortOpen(port, host = null, timeoutMs = 450) {
  const hosts = host ? [host] : ["127.0.0.1", "::1", "localhost"];
  for (const candidate of hosts) {
    if (await probePort(port, candidate, timeoutMs)) return true;
  }
  return false;
}

function normalizeJsonRows(rawText) {
  const text = String(rawText || "").trim();
  if (!text) return [];
  try {
    const parsed = JSON.parse(text);
    return Array.isArray(parsed) ? parsed : [parsed];
  } catch {
    return [];
  }
}

function windowsPortOwners(port) {
  const script = `
$rows = Get-NetTCPConnection -LocalPort ${Number(port)} -State Listen -ErrorAction SilentlyContinue |
  Select-Object -First 8 LocalAddress,LocalPort,State,OwningProcess
$rows | ForEach-Object {
  $proc = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
  [pscustomobject]@{
    pid = $_.OwningProcess
    processName = if ($proc) { $proc.ProcessName } else { "" }
    path = if ($proc) { $proc.Path } else { "" }
    localAddress = $_.LocalAddress
    localPort = $_.LocalPort
    state = $_.State
  }
} | ConvertTo-Json -Compress
`;
  const result = spawnSync("powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], {
    encoding: "utf8",
    timeout: 3500,
    windowsHide: true,
  });
  return normalizeJsonRows(result.stdout).map((item) => ({
    pid: Number(item.pid || 0) || null,
    processName: String(item.processName || ""),
    path: String(item.path || ""),
    localAddress: String(item.localAddress || ""),
    localPort: Number(item.localPort || port),
    state: String(item.state || ""),
  }));
}

function unixPortOwners(port) {
  const result = spawnSync("sh", ["-lc", `lsof -nP -iTCP:${Number(port)} -sTCP:LISTEN 2>/dev/null | tail -n +2`], {
    encoding: "utf8",
    timeout: 2500,
  });
  return String(result.stdout || "")
    .trim()
    .split(/\r?\n/)
    .filter(Boolean)
    .slice(0, 8)
    .map((line) => {
      const parts = line.trim().split(/\s+/);
      return {
        pid: Number(parts[1] || 0) || null,
        processName: parts[0] || "",
        path: "",
        localAddress: "",
        localPort: Number(port),
        state: "LISTEN",
      };
    });
}

export function getPortOwners(port) {
  const numeric = Number(port);
  if (!Number.isInteger(numeric) || numeric <= 0) return [];
  try {
    return process.platform === "win32" ? windowsPortOwners(numeric) : unixPortOwners(numeric);
  } catch {
    return [];
  }
}
