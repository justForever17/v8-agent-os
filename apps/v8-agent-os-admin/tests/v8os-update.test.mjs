import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  V8OS_UPDATE_CONTRACT,
  clearV8OSUpdateCache,
  getV8OSUpdateState,
  readReleaseIdentity,
  selectV8OSUpdate,
} from "../src/lib/server/v8os-update.ts";

const CURRENT_VERSION = "2026.08.09.3";
const UPDATE_VERSION = "2026.08.10.1";

function writeManifest(root) {
  const manifestPath = path.join(root, "release-manifest.json");
  fs.writeFileSync(manifestPath, JSON.stringify({
    schema: 2,
    release: {
      version: CURRENT_VERSION,
      channel: "preview",
      tag: `v8-os-v${CURRENT_VERSION}`,
    },
    products: {
      desktop: {
        enabled: true,
        targets: Object.fromEntries(
          ["windows-x64", "windows-arm64", "macos-x64", "macos-arm64", "linux-x64", "linux-arm64"]
            .map((target) => [target, { enabled: true }]),
        ),
      },
    },
  }));
  return manifestPath;
}

function installerAssets(version, platform, arch) {
  const prefix = `V8-Agent-OS-preview-${version}`;
  const names = {
    win32: [`${prefix}-win-${arch}-setup.exe`],
    darwin: [`${prefix}-macos-${arch}.dmg`],
    linux: [`${prefix}-linux-${arch}.AppImage`, `${prefix}-linux-${arch}.deb`],
  }[platform];
  return ["SHA256SUMS.txt", ...names].map((name) => ({ name, size: 128, state: "uploaded" }));
}

function responseWithUrl(body, url, init = {}) {
  const bytes = Buffer.from(body, "utf8");
  const response = new Response(bytes, {
    status: 200,
    ...init,
    headers: {
      "content-length": String(bytes.byteLength),
      ...(init.headers || {}),
    },
  });
  Object.defineProperty(response, "url", { value: url });
  return response;
}

function checksumBody(version, platform, arch) {
  return installerAssets(version, platform, arch)
    .filter((asset) => asset.name !== "SHA256SUMS.txt")
    .map((asset, index) => `${String(index + 1).padStart(64, "a")}  ${asset.name}`)
    .join("\n");
}

function release(version, platform, arch, assets = installerAssets(version, platform, arch)) {
  const tag = `v8-os-v${version}`;
  return {
    tag_name: tag,
    draft: false,
    prerelease: true,
    html_url: `https://github.com/justForever17/v8-agent-os/releases/tag/${tag}`,
    assets,
  };
}

test("version selection requires a complete current-platform installer on every desktop target", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-admin-update-"));
  const manifestPath = writeManifest(root);
  try {
    for (const [platform, arch] of [
      ["win32", "x64"],
      ["win32", "arm64"],
      ["darwin", "x64"],
      ["darwin", "arm64"],
      ["linux", "x64"],
      ["linux", "arm64"],
    ]) {
      const identity = readReleaseIdentity({ manifestPath, platform, arch });
      const result = selectV8OSUpdate([release(UPDATE_VERSION, platform, arch)], identity, "2026-08-10T00:00:00.000Z");
      assert.equal(result.status, "available", `${platform}-${arch}`);
      assert.equal(result.currentVersion, CURRENT_VERSION);
      assert.equal(result.latestVersion, UPDATE_VERSION);
      assert.equal(result.platformTarget, `${{ win32: "windows", darwin: "macos", linux: "linux" }[platform]}-${arch}`);
      assert.equal(result.releaseUrl, `https://github.com/justForever17/v8-agent-os/releases/tag/v8-os-v${UPDATE_VERSION}`);
    }
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("a partial Linux release is visible but cannot enable the upgrade action", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-admin-update-"));
  const manifestPath = writeManifest(root);
  try {
    const identity = readReleaseIdentity({ manifestPath, platform: "linux", arch: "x64" });
    const incompleteAssets = installerAssets(UPDATE_VERSION, "linux", "x64")
      .filter((asset) => !String(asset.name).endsWith(".deb"));
    const result = selectV8OSUpdate(
      [release(UPDATE_VERSION, "linux", "x64", incompleteAssets)],
      identity,
      "2026-08-10T00:00:00.000Z",
    );
    assert.equal(result.status, "incompatible");
    assert.equal(result.latestVersion, UPDATE_VERSION);
    assert.equal(result.compatibleVersion, null);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("browser fallback coalesces concurrent checks and honors the short server TTL", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-admin-update-"));
  const manifestPath = writeManifest(root);
  clearV8OSUpdateCache();
  let fetchCount = 0;
  let resolveFetch;
  const fetchImpl = async (url, options) => {
    fetchCount += 1;
    assert.equal(options.credentials, "omit");
    assert.equal(Object.keys(options.headers).some((name) => name.toLowerCase() === "authorization"), false);
    if (url !== V8OS_UPDATE_CONTRACT.releasesApiUrl) {
      assert.equal(options.redirect, "follow");
      assert.equal(url, `https://github.com/justForever17/v8-agent-os/releases/download/v8-os-v${UPDATE_VERSION}/SHA256SUMS.txt`);
      return responseWithUrl(checksumBody(UPDATE_VERSION, "win32", "x64"), url, {
        headers: { "content-type": "text/plain" },
      });
    }
    assert.equal(options.redirect, "error");
    return new Promise((resolve) => {
      resolveFetch = () => resolve(responseWithUrl(
        JSON.stringify([release(UPDATE_VERSION, "win32", "x64")]),
        V8OS_UPDATE_CONTRACT.releasesApiUrl,
        { headers: { "content-type": "application/json" } },
      ));
    });
  };
  try {
    const first = getV8OSUpdateState({ manifestPath, platform: "win32", arch: "x64", fetchImpl, now: 1_000 });
    const concurrentForce = getV8OSUpdateState({ force: true, manifestPath, platform: "win32", arch: "x64", fetchImpl, now: 1_000 });
    assert.equal(fetchCount, 1);
    resolveFetch();
    const [left, right] = await Promise.all([first, concurrentForce]);
    assert.equal(left.status, "available");
    assert.deepEqual(right, left);
    assert.equal(fetchCount, 2);

    const cached = await getV8OSUpdateState({ manifestPath, platform: "win32", arch: "x64", fetchImpl, now: 1_001 });
    assert.equal(cached.status, "available");
    assert.equal(fetchCount, 2);
    assert.equal(V8OS_UPDATE_CONTRACT.cacheTtlMs, 300_000);
    assert.equal(V8OS_UPDATE_CONTRACT.requestTimeoutMs, 5_000);
  } finally {
    clearV8OSUpdateCache();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("browser fallback streams bounded responses and rejects missing platform checksums", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-admin-update-"));
  const manifestPath = writeManifest(root);
  try {
    clearV8OSUpdateCache();
    const missingChecksumFetch = async (url) => url === V8OS_UPDATE_CONTRACT.releasesApiUrl
      ? responseWithUrl(
          JSON.stringify([release(UPDATE_VERSION, "linux", "arm64")]),
          url,
          { headers: { "content-type": "application/json" } },
        )
      : responseWithUrl(`${"a".repeat(64)}  unrelated.txt`, url, { headers: { "content-type": "text/plain" } });
    const missingChecksum = await getV8OSUpdateState({
      force: true,
      manifestPath,
      platform: "linux",
      arch: "arm64",
      fetchImpl: missingChecksumFetch,
      now: 2_000,
    });
    assert.equal(missingChecksum.status, "unavailable");
    assert.equal(missingChecksum.errorCode, "checksum_entry_missing");

    clearV8OSUpdateCache();
    let cancelled = false;
    const oversizedFetch = async () => {
      const stream = new ReadableStream({
        start(controller) {
          controller.enqueue(new Uint8Array(1024 * 1024 + 1));
        },
        cancel() {
          cancelled = true;
        },
      });
      return new Response(stream, { status: 200 });
    };
    const oversized = await getV8OSUpdateState({
      force: true,
      manifestPath,
      platform: "win32",
      arch: "x64",
      fetchImpl: oversizedFetch,
      now: 3_000,
    });
    assert.equal(oversized.status, "unavailable");
    assert.equal(oversized.errorCode, "release_response_too_large");
    assert.equal(cancelled, true);
  } finally {
    clearV8OSUpdateCache();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("Topbar prefers the Shell update authority and keeps browser fallback read-only", () => {
  const adminRoot = path.resolve(import.meta.dirname, "..");
  const topbar = fs.readFileSync(path.join(adminRoot, "src/components/layout/Topbar.tsx"), "utf8");
  const shellApi = fs.readFileSync(path.join(adminRoot, "src/components/layout/ShellWindowControls.tsx"), "utf8");
  const route = fs.readFileSync(path.join(adminRoot, "src/app/api/v8os-update/route.ts"), "utf8");
  const webSurfaceRoute = fs.readFileSync(
    path.join(adminRoot, "src/app/api/client/web-surface/route.ts"),
    "utf8",
  );

  assert.match(shellApi, /getUpdateStatus\?: \(\) => Promise<ShellUpdateStatus>/);
  assert.match(shellApi, /checkForUpdates\?: \(\) => Promise<ShellUpdateStatus>/);
  assert.match(shellApi, /openUpdateRelease\?: \(\) => Promise<boolean>/);
  assert.match(topbar, /shell\.getUpdateStatus\(\)/);
  assert.match(topbar, /shell\.checkForUpdates\(\)/);
  assert.match(topbar, /shell\.openUpdateRelease\(\)/);
  assert.match(topbar, /V8OS_UPDATE_CACHE_TTL_MS = 5 \* 60 \* 1000/);
  assert.match(topbar, /<ScrollArea/);
  assert.match(topbar, /h-\[calc\(100dvh-5\.5rem\)\] max-h-\[42rem\]/);
  const updateLoader = topbar.slice(topbar.indexOf("const loadV8OSUpdateState"), topbar.indexOf("const closePanels"));
  assert.doesNotMatch(updateLoader, /setInterval|setTimeout/);
  assert.match(route, /resolveUserEmail/);
  assert.match(route, /Cache-Control/);
  assert.doesNotMatch(route, /export async function (?:POST|PUT|PATCH|DELETE)/);
  assert.match(topbar, /href: WEB_CHAT_SURFACE_URL/);
  assert.match(topbar, /WEB_CHAT_SURFACE_URL = "\/api\/client\/web-surface"/);
  assert.match(webSurfaceRoute, /process\.env\.V8_WEB_BASE_URL/);
  assert.match(webSurfaceRoute, /LOOPBACK_HOSTS/);
  assert.match(webSurfaceRoute, /NextResponse\.redirect\(resolveGovernedWebSurfaceUrl\(\), 307\)/);
  assert.doesNotMatch(topbar, /http:\/\/localhost:9527\/chat/);
});
