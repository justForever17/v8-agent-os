#!/usr/bin/env node
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { Readable, Transform } from "node:stream";
import { pipeline } from "node:stream/promises";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, "..", "..");
const outputRoot = path.join(
  repoRoot,
  "apps",
  "v8-agent-os-shell",
  ".release-prerequisites",
);

const GIT_FOR_WINDOWS_CONTRACT = {
  version: "2.55.0.5",
  releaseTag: "v2.55.0.windows.5",
  targets: {
    "windows-x64": {
      file: "Git-2.55.0.5-64-bit.exe",
      size: 65_343_712,
      sha256: "d065a4e23c3d9a6b5073d609b5be0830227ec3ca053c083ba385061ddfaf94c6",
    },
    "windows-arm64": {
      file: "Git-2.55.0.5-arm64.exe",
      size: 63_259_960,
      sha256: "c955de342b1465bc637f0e71fddf4e28e8d0b829668ec1866ab32a839303e8e3",
    },
  },
};

function argValue(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? String(process.argv[index + 1] || "") : "";
}

function sourceUrl(contract) {
  return `https://github.com/git-for-windows/git/releases/download/${GIT_FOR_WINDOWS_CONTRACT.releaseTag}/${contract.file}`;
}

function sha256File(filePath) {
  const hash = createHash("sha256");
  const descriptor = fs.openSync(filePath, "r");
  const buffer = Buffer.allocUnsafe(1024 * 1024);
  try {
    let bytesRead = 0;
    do {
      bytesRead = fs.readSync(descriptor, buffer, 0, buffer.length, null);
      if (bytesRead > 0) hash.update(buffer.subarray(0, bytesRead));
    } while (bytesRead > 0);
  } finally {
    fs.closeSync(descriptor);
  }
  return hash.digest("hex");
}

function isVerified(filePath, contract) {
  return fs.existsSync(filePath)
    && fs.statSync(filePath).size === contract.size
    && sha256File(filePath) === contract.sha256;
}

function verifyContract() {
  for (const [target, contract] of Object.entries(GIT_FOR_WINDOWS_CONTRACT.targets)) {
    if (!/^windows-(x64|arm64)$/.test(target)) throw new Error(`Invalid target ${target}`);
    if (!/^[a-f0-9]{64}$/.test(contract.sha256)) throw new Error(`Invalid SHA-256 for ${target}`);
    if (!Number.isSafeInteger(contract.size) || contract.size <= 0) throw new Error(`Invalid size for ${target}`);
    const parsed = new URL(sourceUrl(contract));
    if (parsed.protocol !== "https:" || parsed.hostname !== "github.com") {
      throw new Error(`Untrusted Git prerequisite source for ${target}`);
    }
  }
}

async function download(contract, targetPath) {
  let lastError = null;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    const temporaryPath = `${targetPath}.download`;
    fs.rmSync(temporaryPath, { force: true });
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10 * 60_000);
    let received = 0;
    try {
      const response = await fetch(sourceUrl(contract), {
        redirect: "follow",
        cache: "no-store",
        signal: controller.signal,
      });
      if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
      const limiter = new Transform({
        transform(chunk, _encoding, callback) {
          received += Buffer.byteLength(chunk);
          callback(received > contract.size ? new Error("asset_size_exceeded") : null, chunk);
        },
      });
      await pipeline(
        Readable.fromWeb(response.body),
        limiter,
        fs.createWriteStream(temporaryPath, { flags: "wx" }),
        { signal: controller.signal },
      );
      if (!isVerified(temporaryPath, contract)) throw new Error("size_or_sha256_mismatch");
      fs.renameSync(temporaryPath, targetPath);
      return;
    } catch (error) {
      lastError = error;
      fs.rmSync(temporaryPath, { force: true });
      if (attempt < 3) await new Promise((resolve) => setTimeout(resolve, attempt * 2_000));
    } finally {
      clearTimeout(timeout);
    }
  }
  throw lastError || new Error("Git prerequisite download failed");
}

try {
  verifyContract();
  if (process.argv.includes("--verify-contract")) {
    console.log(`Verified pinned Git for Windows ${GIT_FOR_WINDOWS_CONTRACT.version} contract.`);
    process.exit(0);
  }

  const target = argValue("--target");
  const contract = GIT_FOR_WINDOWS_CONTRACT.targets[target];
  if (!contract) throw new Error(`Unsupported --target ${JSON.stringify(target)}`);
  fs.mkdirSync(outputRoot, { recursive: true });
  const targetPath = path.join(outputRoot, contract.file);
  const seedRoot = argValue("--seed-root") ? path.resolve(argValue("--seed-root")) : "";
  const seedPath = seedRoot ? path.join(seedRoot, contract.file) : "";
  if (isVerified(targetPath, contract)) {
    // Reuse the verified local release cache.
  } else if (seedPath && isVerified(seedPath, contract)) {
    fs.copyFileSync(seedPath, targetPath);
  } else {
    await download(contract, targetPath);
  }
  if (!isVerified(targetPath, contract)) throw new Error("Prepared Git prerequisite failed verification");
  fs.writeFileSync(
    path.join(outputRoot, "manifest.json"),
    `${JSON.stringify({ version: 1, target, gitForWindows: { ...contract, sourceUrl: sourceUrl(contract) } }, null, 2)}\n`,
    "utf8",
  );
  console.log(`Prepared verified offline Git prerequisite for ${target}.`);
} catch (error) {
  console.error(error instanceof Error ? error.stack || error.message : String(error));
  process.exit(1);
}
