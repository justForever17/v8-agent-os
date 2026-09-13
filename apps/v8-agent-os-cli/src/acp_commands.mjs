import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { COMPONENTS } from "./components.mjs";
import { ENGINE_DIR } from "./paths.mjs";
import { readRuntimePorts } from "./runtime_ports.mjs";

export function acpLaunchSpec() {
  const engine = COMPONENTS.engine.command();
  const consolePeer = path.join(path.dirname(engine.command), "python.exe");
  const command = path.basename(engine.command).toLowerCase() === "pythonw.exe" && fs.existsSync(consolePeer)
    ? consolePeer : engine.command;
  const script = path.join(ENGINE_DIR, "scripts", "v8os_acp_agent.py");
  if (!fs.existsSync(script)) throw new Error("ACP bridge is missing from this installation.");
  return { command, args: ["-X", "utf8", script], cwd: ENGINE_DIR };
}

export async function commandAcp(args) {
  if (args.length) throw new Error("v8os acp takes no arguments. Configure the optional V8OS_ADMIN_URL for a different local Admin.");
  const spec = acpLaunchSpec();
  const ports = readRuntimePorts();
  return new Promise((resolve, reject) => {
    const child = spawn(spec.command, spec.args, {
      cwd: spec.cwd, stdio: "inherit", windowsHide: true,
      env: { ...process.env, V8OS_ADMIN_URL: process.env.V8OS_ADMIN_URL || `http://127.0.0.1:${ports.admin}` },
    });
    child.once("error", reject);
    child.once("exit", (code) => { process.exitCode = code ?? 1; resolve(); });
  });
}
