import { existsSync } from "node:fs";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { createRequire } from "node:module";

export async function commandTui(args) {
  const local = fileURLToPath(new URL("../../v8-agent-os-tui/bin/v8os-tui.mjs", import.meta.url));
  let entry = existsSync(local) ? local : "";
  if (!entry) {
    try { entry = createRequire(import.meta.url).resolve("@v8/agent-os-tui/bin/v8os-tui.mjs"); } catch { /* separate global prefix below */ }
  }
  if (!entry && process.platform === "win32") {
    entry = (process.env.PATH || "").split(path.delimiter)
      .map(dir => path.join(dir, "node_modules", "@v8", "agent-os-tui", "bin", "v8os-tui.mjs"))
      .find(candidate => existsSync(candidate)) || "";
  }
  const child = spawn(entry ? process.execPath : "v8os-tui", entry ? [entry, ...args] : args, { stdio: "inherit", shell: false });
  return new Promise((resolve) => {
    child.once("error", () => { console.error("请从 V8OS Release 下载 TUI 的 .tgz 包，再运行 npm install -g ./V8OS-TUI-<版本>.tgz；当前尚未上架 npm registry。"); process.exitCode = 1; resolve(); });
    child.once("exit", (code) => { process.exitCode = code ?? 1; resolve(); });
  });
}
