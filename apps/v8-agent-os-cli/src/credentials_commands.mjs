import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { ENGINE_DIR, STATE_ROOT } from "./paths.mjs";

function enginePython() {
  if (process.env.V8_ENGINE_PYTHON) return process.env.V8_ENGINE_PYTHON;
  const candidates = process.platform === "win32"
    ? [path.join(ENGINE_DIR, ".venv", "Scripts", "python.exe"), path.join(ENGINE_DIR, ".python", "python.exe")]
    : [path.join(ENGINE_DIR, ".venv", "bin", "python3"), path.join(ENGINE_DIR, ".venv", "bin", "python"), path.join(ENGINE_DIR, ".python", "bin", "python3")];
  return candidates.find((candidate) => fs.existsSync(candidate)) || (process.platform === "win32" ? "python" : "python3");
}

function configuredKeyPath(keyFile = "") {
  const value = String(keyFile || process.env.V8_AGENT_OS_CREDENTIAL_KEY_FILE || "").trim();
  if (!value) throw new Error("credentials init requires --key-file or V8_AGENT_OS_CREDENTIAL_KEY_FILE");
  if (!path.isAbsolute(value)) throw new Error("--key-file must be an absolute path");
  return path.resolve(value);
}

export function serverCredentialStatus(keyFile = "") {
  const value = configuredKeyPath(keyFile);
  return {
    platform: process.platform,
    supportedProductionPlatform: process.platform === "linux",
    keyFile: value,
    exists: fs.existsSync(value),
    action: fs.existsSync(value) ? "existing_key_preserved" : "run_credentials_init",
  };
}

export function initializeServerCredentials(keyFile = "") {
  const value = configuredKeyPath(keyFile);
  const script = [
    "import json, sys",
    "from core.security.server_credentials import LinuxServerCredentialBackend",
    "try:",
    "    created = LinuxServerCredentialBackend.initialize_key(sys.argv[1], state_root=sys.argv[2])",
    "    print(json.dumps({'ok': True, 'path': str(created)}, ensure_ascii=False))",
    "except Exception as exc:",
    "    print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))",
    "    raise SystemExit(2)",
  ].join("\n");
  const result = spawnSync(enginePython(), ["-X", "utf8", "-c", script, value, STATE_ROOT], {
    cwd: ENGINE_DIR,
    env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" },
    encoding: "utf8",
    timeout: 10_000,
    windowsHide: true,
  });
  const output = String(result.stdout || "").trim().split(/\r?\n/).filter(Boolean).pop() || "";
  let payload;
  try { payload = JSON.parse(output); } catch { payload = { ok: false, error: "Engine Python returned invalid initialization status" }; }
  if (result.error) throw new Error(`credentials init failed: ${result.error.message}`);
  if (result.status !== 0 || !payload.ok) throw new Error(String(payload.error || "credentials init failed"));
  return {
    created: true,
    path: payload.path,
    supportedProductionPlatform: process.platform === "linux",
    instructions: "Set V8_AGENT_OS_CREDENTIAL_KEY_FILE to this absolute path before starting Engine; the key file is never printed or replaced.",
  };
}

export const defaultCredentialKeyPath = path.join(STATE_ROOT, "credentials", "v8-agent-os-credential-key");
