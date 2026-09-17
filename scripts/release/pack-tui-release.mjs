#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { loadReleaseManifest, toSemver, validateReleaseProjections } from "./release-manifest.mjs";
import { validateTuiTgzIntegrity } from "./prepare-release.mjs";
import { verifyArchiveIdentity } from "./prepare-unified-release-assets.mjs";

export function packTuiRelease({ repoRoot, outputDir, sourceCommit }) {
  if (!/^[a-f0-9]{40}$/.test(sourceCommit || "")) throw new Error("An exact source commit is required for TUI packaging");
  const actualCommit = execFileSync("git", ["rev-parse", "HEAD"], { cwd: repoRoot, encoding: "utf8" }).trim();
  if (actualCommit !== sourceCommit) throw new Error("TUI package source commit differs from the checkout");
  const dirty = execFileSync("git", ["status", "--porcelain", "--untracked-files=all", "--", "apps/v8-agent-os-tui", "apps/v8-agent-os-cli", "packages/session-realtime", "release-manifest.json", "VERSION"], { cwd: repoRoot, encoding: "utf8" }).trim();
  if (dirty) throw new Error("TUI package source inputs are modified; commit the release candidate before packing");
  const { manifest } = loadReleaseManifest(path.join(repoRoot, "release-manifest.json"));
  validateReleaseProjections(manifest, repoRoot);
  validateTuiTgzIntegrity(repoRoot);
  const source = path.join(repoRoot, "apps/v8-agent-os-tui");
  const pkg = JSON.parse(fs.readFileSync(path.join(source, "package.json"), "utf8"));
  const version = manifest.release.version;
  const output = path.resolve(outputDir, `V8OS-TUI-${version}.tgz`);
  if (fs.existsSync(output)) throw new Error(`Refusing to overwrite ${output}`);
  const stage = fs.mkdtempSync(path.join(os.tmpdir(), "v8-tui-pack-"));
  try {
    // Stage only public package files; do not rewrite the TUI source manifest.
    for (const relative of ["bin", "dist", "README.md", "LICENSE", "NOTICE.md"]) {
      fs.cpSync(path.join(source, relative), path.join(stage, relative), { recursive: true, dereference: false });
    }
    pkg.version = toSemver(version);
    pkg.v8Release = { version, sourceCommit };
    delete pkg.scripts;
    delete pkg.devDependencies;
    fs.writeFileSync(path.join(stage, "package.json"), `${JSON.stringify(pkg, null, 2)}\n`);
    const npmCli = [
      path.join(path.dirname(process.execPath), "node_modules/npm/bin/npm-cli.js"),
      path.resolve(path.dirname(process.execPath), "../lib/node_modules/npm/bin/npm-cli.js"),
    ].find((filename) => fs.existsSync(filename));
    if (!npmCli) throw new Error("Cannot find the npm CLI beside the configured Node runtime");
    const packed = JSON.parse(execFileSync(process.execPath, [npmCli, "pack", "--ignore-scripts", "--json"], { cwd: stage, encoding: "utf8" }));
    if (packed.length !== 1 || path.basename(packed[0].filename) !== packed[0].filename) throw new Error("npm pack did not produce one local tarball");
    const archive = path.join(stage, packed[0].filename);
    verifyArchiveIdentity(archive, { product: "tui", version, sourceCommit });
    fs.mkdirSync(path.dirname(output), { recursive: true });
    fs.copyFileSync(archive, output, fs.constants.COPYFILE_EXCL);
    return output;
  } finally { fs.rmSync(stage, { recursive: true, force: true }); }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const args = {};
    for (let index = 2; index < process.argv.length; index += 2) {
      const key = process.argv[index];
      if (!["--output", "--source-commit"].includes(key) || args[key] || !process.argv[index + 1] || process.argv[index + 1].startsWith("--")) throw new Error(`Invalid TUI pack argument: ${key}`);
      args[key] = process.argv[index + 1];
    }
    if (!args["--output"] || !args["--source-commit"]) throw new Error("Usage: pack-tui-release.mjs --output <directory> --source-commit <sha>");
    const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
    console.log(packTuiRelease({ repoRoot, outputDir: args["--output"], sourceCommit: args["--source-commit"] }));
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
