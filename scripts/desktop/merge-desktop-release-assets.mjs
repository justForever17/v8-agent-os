#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";

function argValue(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? String(process.argv[index + 1] || "") : "";
}

try {
  const inputDir = path.resolve(argValue("--input-dir"));
  if (!inputDir || !fs.existsSync(inputDir)) throw new Error("Use --input-dir <downloaded desktop release assets>.");
  const checksumFiles = fs.readdirSync(inputDir)
    .filter((name) => /^SHA256SUMS-(windows|macos|linux)-(x64|arm64)\.txt$/.test(name))
    .sort();
  if (!checksumFiles.length) throw new Error(`No platform checksum manifests found in ${inputDir}`);

  const seen = new Set();
  const lines = [];
  for (const name of checksumFiles) {
    const content = fs.readFileSync(path.join(inputDir, name), "utf8");
    for (const line of content.split(/\r?\n/).map((value) => value.trim()).filter(Boolean)) {
      const match = /^([a-f0-9]{64})\s{2}(.+)$/.exec(line);
      if (!match) throw new Error(`Invalid checksum line in ${name}: ${line}`);
      if (seen.has(match[2])) throw new Error(`Duplicate desktop release asset checksum: ${match[2]}`);
      if (!fs.existsSync(path.join(inputDir, match[2]))) throw new Error(`Checksum references missing release asset: ${match[2]}`);
      seen.add(match[2]);
      lines.push(line);
    }
  }
  fs.writeFileSync(path.join(inputDir, "SHA256SUMS.txt"), `${lines.sort().join("\n")}\n`, "utf8");
  console.log(lines.sort().join("\n"));
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}
