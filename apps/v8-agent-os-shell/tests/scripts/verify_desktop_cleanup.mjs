import fs from "node:fs";
import net from "node:net";
import path from "node:path";

function argument(name, fallback = "") {
  const index = process.argv.indexOf(name);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

const stateRoot = path.resolve(argument("--state-root", process.env.V8_AGENT_OS_HOME || ""));
const timeoutMs = Number(argument("--timeout-ms", "8000"));
const ports = argument("--ports", "9530,9528,9527")
  .split(",")
  .map((value) => Number(value.trim()))
  .filter((value) => Number.isInteger(value) && value > 0 && value <= 65_535);
if (!stateRoot || stateRoot === path.parse(stateRoot).root) {
  throw new Error("Desktop cleanup verification requires a bounded state root.");
}
if (!Number.isFinite(timeoutMs) || timeoutMs < 1 || timeoutMs > 60_000 || !ports.length) {
  throw new Error("Desktop cleanup verification arguments are invalid.");
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const portOpen = (port) => new Promise((resolve) => {
  const socket = net.createConnection({ host: "127.0.0.1", port });
  const finish = (open) => {
    socket.destroy();
    resolve(open);
  };
  socket.setTimeout(300, () => finish(false));
  socket.once("connect", () => finish(true));
  socket.once("error", () => finish(false));
});

function liveDescriptorPids(relativePath, fields) {
  const filePath = path.join(stateRoot, ...relativePath);
  if (!fs.existsSync(filePath)) return [];
  try {
    const descriptor = JSON.parse(fs.readFileSync(filePath, "utf8"));
    return fields.map((field) => {
      const pid = Number(descriptor?.[field]);
      if (!Number.isInteger(pid) || pid <= 0) return { filePath, field, pid: null, alive: false };
      let alive = true;
      try {
        process.kill(pid, 0);
      } catch {
        alive = false;
      }
      return { filePath, field, pid, alive };
    });
  } catch {
    return fields.map((field) => ({ filePath, field, pid: null, alive: false }));
  }
}

const deadline = Date.now() + timeoutMs;
let openPorts = [];
let liveDescriptors = [];
do {
  openPorts = [];
  for (const port of ports) {
    if (await portOpen(port)) openPorts.push(port);
  }
  liveDescriptors = [
    ...liveDescriptorPids(["runtime", "shell-control.json"], ["pid"]),
    ...liveDescriptorPids(["runtime", "desktop-pet.json"], ["pid", "serverPid"]),
  ].filter((item) => item.alive);
  if (!openPorts.length && !liveDescriptors.length) break;
  await sleep(250);
} while (Date.now() < deadline);

if (openPorts.length || liveDescriptors.length) {
  const details = [
    openPorts.length ? `ports=${openPorts.join(",")}` : "",
    liveDescriptors.length
      ? `pids=${liveDescriptors.map((item) => `${item.field}:${item.pid}`).join(",")}`
      : "",
  ].filter(Boolean).join(" ");
  throw new Error(`Packaged desktop cleanup left managed runtime state alive: ${details}`);
}

console.log("V8OS_PACKAGED_DESKTOP_CLEANUP_OK");
