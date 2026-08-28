#!/usr/bin/env node
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";


const __filename = fileURLToPath(import.meta.url);
const repoRoot = path.resolve(path.dirname(__filename), "..", "..");
const engineRoot = path.join(repoRoot, "apps", "v8-agent-os-engine");
const catalogPath = path.join(engineRoot, "runtimes", "plugin_manager", "resources", "catalog.json");
const outputRoot = path.join(engineRoot, ".plugin-release-assets");
const TARGETS = {
  "windows-x64": { platform: "windows", architecture: "amd64" },
  "windows-arm64": { platform: "windows", architecture: "arm64" },
  "macos-x64": { platform: "macos", architecture: "amd64" },
  "macos-arm64": { platform: "macos", architecture: "arm64" },
  "linux-x64": { platform: "linux", architecture: "amd64" },
  "linux-arm64": { platform: "linux", architecture: "arm64" },
};

function argValue(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? String(process.argv[index + 1] || "") : "";
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function selectedAssets(catalog, target) {
  const result = [];
  for (const plugin of catalog.plugins || []) {
    for (const profile of plugin.cliProfiles || []) {
      const install = profile.install || {};
      if (install.argv?.[0] !== "v8-managed-download") continue;
      if (!(profile.platforms || []).includes(target.platform)) continue;
      if (!(profile.architectures || []).includes(target.architecture)) continue;
      if (!install.downloadUrl || !/^[a-f0-9]{64}$/i.test(String(install.downloadSha256 || ""))) {
        throw new Error(`Managed download ${plugin.id}/${profile.id} has no pinned URL and SHA-256`);
      }
      if (!String(install.downloadUrl).startsWith("https://github.com/")) {
        throw new Error(`Managed download ${plugin.id}/${profile.id} is not an official GitHub release URL`);
      }
      if (Number(install.estimatedDownloadMb || 0) > 20) continue;
      result.push({
        pluginId: plugin.id,
        componentId: profile.id,
        url: String(install.downloadUrl),
        sha256: String(install.downloadSha256).toLowerCase(),
      });
    }
  }
  return result;
}

async function download(url) {
  let lastError = null;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 120_000);
    try {
      const response = await fetch(url, { redirect: "follow", signal: controller.signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return Buffer.from(await response.arrayBuffer());
    } catch (error) {
      lastError = error;
      if (attempt < 3) await new Promise((resolve) => setTimeout(resolve, attempt * 1_000));
    } finally {
      clearTimeout(timeout);
    }
  }
  throw lastError || new Error("managed plugin asset download failed");
}

try {
  const targetId = argValue("--target");
  const target = TARGETS[targetId];
  if (!target) throw new Error(`Unsupported --target ${JSON.stringify(targetId)}`);
  const catalog = JSON.parse(fs.readFileSync(catalogPath, "utf8"));
  const assets = selectedAssets(catalog, target);
  fs.rmSync(outputRoot, { recursive: true, force: true });
  fs.mkdirSync(outputRoot, { recursive: true });
  const manifest = [];
  for (const asset of assets) {
    const bytes = await download(asset.url);
    const actual = sha256(bytes);
    if (actual !== asset.sha256) {
      throw new Error(`Managed plugin asset SHA-256 mismatch for ${asset.pluginId}/${asset.componentId}`);
    }
    const targetPath = path.join(outputRoot, `${asset.sha256}.asset`);
    fs.writeFileSync(targetPath, bytes, { mode: 0o444 });
    manifest.push({
      pluginId: asset.pluginId,
      componentId: asset.componentId,
      sha256: asset.sha256,
      size: bytes.length,
      file: path.basename(targetPath),
    });
  }
  fs.writeFileSync(
    path.join(outputRoot, "manifest.json"),
    `${JSON.stringify({ version: 1, target: targetId, assets: manifest }, null, 2)}\n`,
    "utf8",
  );
  console.log(`Prepared ${manifest.length} verified managed plugin asset(s) for ${targetId}.`);
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}
