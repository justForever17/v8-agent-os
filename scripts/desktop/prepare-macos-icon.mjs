#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const repoRoot = path.resolve(path.dirname(__filename), "..", "..");
const source = path.join(repoRoot, "apps", "v8-agent-os-shell", "assets", "icon.png");
const output = path.join(repoRoot, "apps", "v8-agent-os-shell", "assets", "icon.icns");
const iconset = path.join(path.dirname(output), "icon.iconset");

function run(command, args) {
  const result = spawnSync(command, args, { encoding: "utf8", timeout: 120000 });
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  if (result.error || result.status !== 0) {
    throw new Error(`${command} ${args.join(" ")} failed: ${result.error?.message || `exit ${result.status}`}`);
  }
}

try {
  if (process.platform !== "darwin") throw new Error("prepare-macos-icon.mjs must run on macOS.");
  if (!fs.existsSync(source)) throw new Error(`Missing macOS icon source: ${source}`);
  fs.rmSync(iconset, { recursive: true, force: true });
  fs.rmSync(output, { force: true });
  fs.mkdirSync(iconset, { recursive: true });
  for (const size of [16, 32, 128, 256, 512]) {
    for (const scale of [1, 2]) {
      const pixels = size * scale;
      const suffix = scale === 2 ? "@2x" : "";
      run("sips", ["-z", String(pixels), String(pixels), source, "--out", path.join(iconset, `icon_${size}x${size}${suffix}.png`)]);
    }
  }
  run("iconutil", ["-c", "icns", iconset, "-o", output]);
  if (!fs.existsSync(output)) throw new Error(`iconutil did not create ${output}`);
  console.log(`Generated ${output}`);
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
} finally {
  fs.rmSync(iconset, { recursive: true, force: true });
}
