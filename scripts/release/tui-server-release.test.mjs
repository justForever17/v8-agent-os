import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { evaluateReleaseGate, validateReleaseManifest, validateReleaseProjections, resolveReleasePlan, toSemver } from "./release-manifest.mjs";
import { preparationManifest, resolvePreparationIdentity, resolvePreparationRequest, validateTuiTgzIntegrity, updateTuiVersion } from "./prepare-release.mjs";
import { loadReleasePlan, writeGithubOutputs } from "./resolve-release-plan.mjs";
import { prepareUnifiedReleaseAssets, verifyArchiveIdentity, verifyEngineArchiveIdentity, verifyEngineReleaseAssets } from "./prepare-unified-release-assets.mjs";
import { packTuiRelease } from "./pack-tui-release.mjs";

const ROOT = path.resolve(import.meta.dirname, "../..");
const BASE = JSON.parse(fs.readFileSync(path.join(ROOT, "release-manifest.json")));
const VERSION = BASE.release.version;
const COMMIT = "b".repeat(40);
const tui = () => ({ enabled: true, required: true, targets: { npm: { enabled: true, required: true } } });
const json = (filename, value) => { fs.mkdirSync(path.dirname(filename), { recursive: true }); fs.writeFileSync(filename, JSON.stringify(value)); };

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "v8-release-tui-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  for (const item of ["VERSION", "release-manifest.json", "apps/v8-agent-os-shell/package.json", "apps/v8-agent-os-shell/package-lock.json", "apps/v8-agent-os-phone/package.json", "apps/v8-agent-os-phone/package-lock.json", "apps/v8-agent-os-phone/app.json"]) {
    fs.mkdirSync(path.dirname(path.join(root, item)), { recursive: true });
    fs.copyFileSync(path.join(ROOT, item), path.join(root, item));
  }
  const manifest = structuredClone(BASE);
  manifest.products.server.enabled = manifest.products.server.required = false;
  manifest.products.server.reason = "TUI-specific fixture excludes Server";
  manifest.products.tui = tui();
  json(path.join(root, "release-manifest.json"), manifest);
  json(path.join(root, "apps/v8-agent-os-tui/package.json"), tuiPackage());
  json(path.join(root, "apps/v8-agent-os-tui/package-lock.json"), { version: toSemver(VERSION), packages: { "": { version: toSemver(VERSION) } } });
  return { root, manifest, manifestPath: path.join(root, "release-manifest.json") };
}

function tuiPackage() {
  return { name: "@v8-agent-os/v8-agent-os", version: toSemver(VERSION), type: "module", bin: { v8os: "bin/v8os.mjs", "v8os-tui": "bin/v8os-tui.mjs" },
    engines: { node: ">=22" }, files: ["bin", "dist", "LICENSE", "README.md", "NOTICE.md"], v8Release: { version: VERSION, sourceCommit: COMMIT } };
}

function tarFixture(root, filename, entries) {
  const stage = fs.mkdtempSync(path.join(root, "tar-"));
  for (const [name, content] of Object.entries(entries)) {
    fs.mkdirSync(path.dirname(path.join(stage, name)), { recursive: true });
    fs.writeFileSync(path.join(stage, name), content);
  }
  fs.mkdirSync(path.dirname(filename), { recursive: true });
  execFileSync("tar", ["-czf", filename, "-C", stage, ...fs.readdirSync(stage)]);
  return filename;
}

function engineTarFixture(root, filename, identity) {
  const stage = fs.mkdtempSync(path.join(root, "engine-tar-"));
  const prefix = `v8os-engine-${identity.version}-${identity.target}`;
  const write = (relative, content) => {
    const target = path.join(stage, prefix, relative);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, content);
  };
  write("engine-manifest.json", JSON.stringify(identity));
  write("README.md", "portable engine\n");
  write("apps/v8-agent-os-engine/main.py", "# fixture");
  write("apps/v8-agent-os-cli/bin/v8os.mjs", "#!/usr/bin/env node");
  write("apps/v8-agent-os-engine/.python/bin/python3.11", "python fixture");
  fs.symlinkSync("python3.11", path.join(stage, prefix, "apps/v8-agent-os-engine/.python/bin/python3"));
  write("apps/v8-agent-os-engine/.python/v8os-runtime.json", JSON.stringify({ schema: 1, profile: "server", target: identity.target, browserIncluded: true }) + "\n");
  const sums = [];
  for (const file of fs.readdirSync(path.join(stage, prefix), { recursive: true })) {
    const absolute = path.join(stage, prefix, file);
    if (fs.statSync(absolute).isFile() && file !== "SHA256SUMS") sums.push(`${createHash("sha256").update(fs.readFileSync(absolute)).digest("hex")}  ${file.replaceAll(path.sep, "/")}`);
  }
  write("SHA256SUMS", `${sums.join("\n")}\n`);
  fs.mkdirSync(path.dirname(filename), { recursive: true });
  execFileSync("tar", ["-czf", filename, "-C", stage, prefix]);
  return filename;
}

const tuiEntries = (pkg = tuiPackage()) => ({ "package/package.json": JSON.stringify(pkg), "package/bin/v8os.mjs": "#!/usr/bin/env node\nconsole.log('installed fixture')", "package/bin/v8os-tui.mjs": "#!/usr/bin/env node\nconsole.log('installed fixture')", "package/bin/engine-bootstrap.mjs": "export const fixture=true", "package/dist/main.js": "export const fixture=true", "package/dist/core-control.mjs": "export const fixture=true", "package/dist/cli.mjs": "export const fixture=true", "package/LICENSE": "fixture license" });

function packableFixture(t, extraBuild = () => "") {
  const f = fixture(t), source = path.join(f.root, "apps/v8-agent-os-tui");
  const files = {
    "bin/v8os.mjs": "#!/usr/bin/env node\nconsole.log('unified entrypoint');",
    "bin/engine-bootstrap.mjs": "export const bootstrap = true;",
    "bin/v8os-tui.mjs": "#!/usr/bin/env node\nimport { ui } from '../dist/main.js'; console.log(ui);",
    "src/main.js": "export const ui = 'current inline command menu';",
    "src/core-control.mjs": "export const core = true;",
    "src/cli.mjs": "export const cli = true;",
    "scripts/build.mjs": `import fs from 'node:fs';\nfs.mkdirSync('dist', { recursive: true });\nfs.copyFileSync('src/main.js', 'dist/main.js');\nfs.copyFileSync('src/core-control.mjs', 'dist/core-control.mjs');\nfs.copyFileSync('src/cli.mjs', 'dist/cli.mjs');\nfs.copyFileSync('../../LICENSE', 'LICENSE');\n${extraBuild(f.root)}`,
    "README.md": "fixture", "NOTICE.md": "fixture",
  };
  for (const [name, content] of Object.entries(files)) {
    const filename = path.join(source, name);
    fs.mkdirSync(path.dirname(filename), { recursive: true }); fs.writeFileSync(filename, content);
  }
  fs.writeFileSync(path.join(f.root, "LICENSE"), "fixture license");
  json(path.join(f.root, "apps/v8-agent-os-cli/package.json"), { name: "cli-fixture" });
  json(path.join(f.root, "packages/session-realtime/package.json"), { name: "projection-fixture" });
  fs.writeFileSync(path.join(f.root, ".gitignore"), "**/dist/\n**/node_modules/\napps/v8-agent-os-tui/LICENSE\n**/ignored-local.mjs\n");
  execFileSync("git", ["init", "--quiet"], { cwd: f.root });
  execFileSync("git", ["add", "."], { cwd: f.root });
  execFileSync("git", ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture"], { cwd: f.root });
  const sourceCommit = execFileSync("git", ["rev-parse", "HEAD"], { cwd: f.root, encoding: "utf8" }).trim();
  fs.mkdirSync(path.join(source, "dist")); fs.mkdirSync(path.join(source, "node_modules"));
  fs.writeFileSync(path.join(source, "dist/main.js"), "export const ui = 'stale fullscreen menu';");
  fs.writeFileSync(path.join(source, "dist/core-control.mjs"), "export const core = 'stale';");
  fs.writeFileSync(path.join(source, "dist/cli.mjs"), "export const cli = 'stale';");
  fs.writeFileSync(path.join(source, "dist/orphan.js"), "stale compiled file");
  fs.writeFileSync(path.join(source, "node_modules/keep.txt"), "installed toolchain remains intact");
  fs.copyFileSync(path.join(f.root, "LICENSE"), path.join(source, "LICENSE"));
  return { ...f, source, sourceCommit };
}

test("schema2 historical absence and disabled optional products remain compatible", () => {
  const historical = structuredClone(BASE);
  delete historical.products.tui;
  delete historical.products.server;
  assert.equal(validateReleaseManifest(historical), historical);
  assert.equal(resolveReleasePlan(historical).tui.enabled, false);
  const future = structuredClone(BASE);
  future.products.tui = tui();
  future.products.server.enabled = future.products.server.required = true;
  const next = VERSION.replace(/\.\d+$/, (value) => `.${Number(value.slice(1)) + 1}`);
  assert.deepEqual(resolvePreparationIdentity({ manifest: future, version: next }).products, ["desktop", "phone", "server", "tui"]);
  future.products.tui.targets.npm.required = false;
  assert.throws(() => validateReleaseManifest(future), /required target/);
});

test("TUI projections update package and both lock versions and reject drift", (t) => {
  const f = fixture(t);
  validateReleaseProjections(f.manifest, f.root);
  const filename = path.join(f.root, "apps/v8-agent-os-tui/package-lock.json");
  const lock = JSON.parse(fs.readFileSync(filename));
  lock.packages[""].version = "0.0.0";
  json(filename, lock);
  assert.throws(() => validateReleaseProjections(f.manifest, f.root), /TUI package-lock root/);
  json(path.join(f.root, "apps/v8-agent-os-tui/package.json"), { ...tuiPackage(), version: "0.1.0" });
  json(filename, { version: "0.1.0", packages: { "": { version: "0.1.0" } } });
  updateTuiVersion(VERSION, f.root);
  validateReleaseProjections(f.manifest, f.root);
});

test("prepare dry-run accepts a new 0.1.0 TUI, changes nothing, and missing package fails before writes", (t) => {
  const f = fixture(t);
  f.manifest.products.tui.enabled = f.manifest.products.tui.required = false;
  f.manifest.products.tui.reason = "not yet released";
  json(f.manifestPath, f.manifest);
  json(path.join(f.root, "apps/v8-agent-os-tui/package.json"), { ...tuiPackage(), version: "0.1.0" });
  json(path.join(f.root, "apps/v8-agent-os-tui/package-lock.json"), { version: "0.1.0", packages: { "": { version: "0.1.0" } } });
  fs.cpSync(path.join(ROOT, "scripts/release"), path.join(f.root, "scripts/release"), { recursive: true });
  for (const app of ["admin", "web", "desktop-pet"]) json(path.join(f.root, `apps/v8-agent-os-${app}/package-lock.json`), { packages: {} });
  const shared = path.join(f.root, "shared.tgz"); fs.writeFileSync(shared, "shared fixture");
  const phoneLockPath = path.join(f.root, "apps/v8-agent-os-phone/package-lock.json");
  const phoneLock = JSON.parse(fs.readFileSync(phoneLockPath));
  phoneLock.packages["node_modules/@v8/session-realtime"] = { resolved: "file:../../shared.tgz", integrity: "sha512-" + createHash("sha512").update(fs.readFileSync(shared)).digest("base64") };
  json(phoneLockPath, phoneLock);
  execFileSync("git", ["init", "--quiet"], { cwd: f.root });
  const next = VERSION.replace(/\.\d+$/, (value) => `.${Number(value.slice(1)) + 1}`);
  const script = path.join(f.root, "scripts/release/prepare-release.mjs");
  const args = [script, "--version", next, "--enable-tui", "--enable-server"];
  const before = fs.readFileSync(f.manifestPath);
  const beforeVersion = fs.readFileSync(path.join(f.root, "VERSION"));
  const pkg = path.join(f.root, "apps/v8-agent-os-tui/package.json");
  const beforePackage = fs.readFileSync(pkg);
  const plan = execFileSync(process.execPath, args, { cwd: f.root, encoding: "utf8" });
  assert.match(plan, /products: desktop, phone, server, tui/);
  assert.deepEqual(fs.readFileSync(f.manifestPath), before);
  assert.deepEqual(fs.readFileSync(pkg), beforePackage);
  const withoutFlags = execFileSync(process.execPath, [script, "--version", next], { cwd: f.root, encoding: "utf8" });
  assert.match(withoutFlags, /products: desktop, phone\r?\n/);
  fs.unlinkSync(pkg);
  assert.throws(() => execFileSync(process.execPath, args, { cwd: f.root, stdio: "pipe" }), /package\.json/);
  assert.deepEqual(fs.readFileSync(f.manifestPath), before);
  assert.deepEqual(fs.readFileSync(path.join(f.root, "VERSION")), beforeVersion);
  fs.writeFileSync(pkg, beforePackage);
  json(path.join(f.root, "apps/v8-agent-os-tui/package-lock.json"), { version: "0.1.0", packages: {} });
  assert.throws(() => execFileSync(process.execPath, args, { cwd: f.root, stdio: "pipe" }), /both lock version projections/);
  assert.deepEqual(fs.readFileSync(f.manifestPath), before);
  assert.deepEqual(fs.readFileSync(path.join(f.root, "VERSION")), beforeVersion);
});

test("new products can be enabled only while preparing a newer release without changing the current manifest", () => {
  const current = structuredClone(BASE);
  for (const name of ["server", "tui"]) {
    current.products[name].enabled = current.products[name].required = false;
    current.products[name].reason = "not yet released";
  }
  const planned = preparationManifest(current, { "enable-server": true, "enable-tui": true });
  assert.equal(current.products.server.enabled, false);
  assert.equal(current.products.tui.enabled, false);
  assert.equal(planned.release.version, VERSION);
  assert.equal(planned.products.server.enabled, true);
  assert.equal(planned.products.tui.enabled, true);
  assert.equal(planned.products.server.targets["linux-arm64"].enabled, false);
  assert.throws(() => resolvePreparationIdentity({ manifest: planned, version: VERSION }), /newer/);
  assert.throws(() => resolvePreparationRequest({ "from-manifest": true, "enable-tui": true }, BASE), /cannot enable/);
});

test("local source tgz must match both dependency lock and actual sha512", (t) => {
  const f = fixture(t);
  const source = path.join(f.root, "shared.tgz");
  fs.writeFileSync(source, "source package fixture");
  const pkgPath = path.join(f.root, "apps/v8-agent-os-tui/package.json");
  json(pkgPath, { ...tuiPackage(), dependencies: { "@v8/session-realtime": "file:../../shared.tgz" } });
  const lock = { version: toSemver(VERSION), packages: { "": { version: toSemver(VERSION) }, "node_modules/@v8/session-realtime": { resolved: "file:../../shared.tgz", integrity: "sha512-" + createHash("sha512").update(fs.readFileSync(source)).digest("base64") } } };
  json(path.join(f.root, "apps/v8-agent-os-tui/package-lock.json"), lock);
  assert.equal(validateTuiTgzIntegrity(f.root).ok, true);
  fs.appendFileSync(source, "tamper");
  assert.throws(() => validateTuiTgzIntegrity(f.root), /integrity mismatch/);
  lock.packages = { "": { version: toSemver(VERSION) } };
  json(path.join(f.root, "apps/v8-agent-os-tui/package-lock.json"), lock);
  assert.throws(() => validateTuiTgzIntegrity(f.root), /no matching locked/);
});

test("unified plan includes npm TUI; legacy and dry-run never secretly publish it", (t) => {
  const f = fixture(t);
  const plan = loadReleasePlan({ manifestPath: f.manifestPath, tag: f.manifest.release.tag });
  const outputs = path.join(f.root, "outputs");
  writeGithubOutputs(outputs, plan);
  assert.match(fs.readFileSync(outputs, "utf8"), /^tui_enabled=true$/m);
  assert.match(fs.readFileSync(outputs, "utf8"), /^tui_targets_json=\["npm"\]$/m);
  assert.equal(loadReleasePlan({ manifestPath: f.manifestPath }).publish, false);
  const legacy = loadReleasePlan({ manifestPath: f.manifestPath, tag: `v8-os-phone-v${VERSION}` });
  assert.equal(legacy.tui.enabled, false);
  assert.equal(legacy.tui.targets[0].enabled, false);
});

test("the actual release gate blocks every required failure, cancellation and skipped TUI", () => {
  for (const result of ["failure", "cancelled", "skipped", undefined]) {
    assert.throws(() => evaluateReleaseGate({ runBuilds: true, publish: true, products: { tui: { enabled: true, required: true, result } } }), /Required product tui/);
  }
  assert.equal(evaluateReleaseGate({ runBuilds: true, publish: true, products: { tui: { enabled: true, required: true, result: "success" } } }), true);
  assert.equal(evaluateReleaseGate({ runBuilds: false, publish: false, products: { tui: { result: "skipped" } } }), false);
  assert.throws(() => evaluateReleaseGate({ runBuilds: false, publish: true, products: {} }), /validation-only/);
  assert.throws(() => evaluateReleaseGate({ runBuilds: true, publish: true, products: { tui: { enabled: false, result: "success" } } }), /Disabled product/);
});

test("TUI fan-in rejects absent tarball and verifies internal identity before publication", (t) => {
  const f = fixture(t);
  const input = path.join(f.root, "input");
  const file = path.join(input, "tui", `V8OS-TUI-${VERSION}.tgz`);
  const run = (n) => prepareUnifiedReleaseAssets({ manifestPath: f.manifestPath, inputDir: input, outputDir: path.join(f.root, `out${n}`), sourceCommit: COMMIT });
  assert.throws(() => run(1), /Required TUI npm asset/);
  tarFixture(f.root, file, tuiEntries({ ...tuiPackage(), version: "0.0.0" }));
  assert.throws(() => run(2), /TUI archive identity/);
  const missing = tuiEntries(); delete missing["package/dist/main.js"];
  tarFixture(f.root, file, missing);
  assert.throws(() => run(3), /missing member package\/dist\/main.js/);
  tarFixture(f.root, file, tuiEntries());
  // Other required products must still block this otherwise-valid TUI asset.
  assert.throws(() => run(4), /Required Desktop/);
  verifyArchiveIdentity(file, { product: "tui", version: VERSION, sourceCommit: COMMIT });
  assert.throws(() => verifyArchiveIdentity(file, { product: "tui", version: VERSION, sourceCommit: "c".repeat(40) }), /identity/);
  fs.mkdirSync(path.join(input, "desktop"), { recursive: true });
  for (const suffix of ["win-x64-setup.exe", "win-arm64-setup.exe", "macos-x64.dmg", "macos-arm64.dmg", "linux-x64.AppImage", "linux-x64.deb", "linux-arm64.AppImage", "linux-arm64.deb"]) {
    fs.writeFileSync(path.join(input, "desktop", `V8-Agent-OS-preview-${VERSION}-${suffix}`), "synthetic desktop asset");
  }
  fs.mkdirSync(path.join(input, "phone/android"), { recursive: true });
  fs.writeFileSync(path.join(input, "phone/android/app-release.apk"), "synthetic android asset");
  const published = run(5);
  assert.equal(published.assets.length, 10);
  assert.ok(published.assets.includes(`V8OS-TUI-${VERSION}.tgz`));
  const expectedHash = createHash("sha256").update(fs.readFileSync(file)).digest("hex");
  assert.ok(fs.readFileSync(path.join(f.root, "out5/SHA256SUMS.txt"), "utf8").includes(`${expectedHash}  V8OS-TUI-${VERSION}.tgz`));
});

test("server archive checks architecture, source identity, dirty state and payload members", (t) => {
  const f = fixture(t);
  const prefix = `v8os-server-${VERSION}-linux-x64`;
  const identity = { schema: 1, profile: "server", version: VERSION, platform: "linux", arch: "x64", sourceCommit: COMMIT, sourceDirty: false };
  const entries = { [`${prefix}/server-manifest.json`]: JSON.stringify(identity), [`${prefix}/VERSION`]: toSemver(VERSION), [`${prefix}/apps/v8-agent-os-engine/main.py`]: "# fixture", [`${prefix}/apps/v8-agent-os-cli/bin/v8os.mjs`]: "// fixture", [`${prefix}/SHA256SUMS`]: "fixture" };
  const archive = path.join(f.root, "server.tar.gz");
  const verify = () => verifyArchiveIdentity(archive, { product: "server", version: VERSION, target: "linux-x64", sourceCommit: COMMIT });
  for (const change of [{ arch: "arm64" }, { version: "2026.01.01.1" }, { sourceCommit: "d".repeat(40) }, { sourceDirty: true }]) {
    tarFixture(f.root, archive, { ...entries, [`${prefix}/server-manifest.json`]: JSON.stringify({ ...identity, ...change }) });
    assert.throws(verify, /Server archive identity/);
  }
  delete entries[`${prefix}/apps/v8-agent-os-engine/main.py`];
  tarFixture(f.root, archive, entries);
  assert.throws(verify, /missing member/);
});

test("portable Engine fan-in verifies public JSON, internal identity, archive SHA and Python symlink", (t) => {
  const f = fixture(t);
  f.manifest.products.server.enabled = f.manifest.products.server.required = true;
  for (const target of Object.values(f.manifest.products.server.standaloneTargets || {})) {
    target.enabled = false; target.required = false; target.reason = "fixture only verifies linux Engine";
  }
  f.manifest.products.server.targets["linux-x64"].enabled = f.manifest.products.server.targets["linux-x64"].required = true;
  f.manifest.products.server.targets["linux-x64"].standalone = { enabled: true, required: true };
  json(f.manifestPath, f.manifest);
  const input = path.join(f.root, "input"), serverRoot = `v8os-server-${VERSION}-linux-x64`;
  const serverIdentity = { schema: 1, profile: "server", version: VERSION, platform: "linux", arch: "x64", sourceCommit: COMMIT, sourceDirty: false };
  tarFixture(f.root, path.join(input, "server", `V8OS-Server-${VERSION}-linux-x64.tar.gz`), {
    [`${serverRoot}/server-manifest.json`]: JSON.stringify(serverIdentity), [`${serverRoot}/VERSION`]: toSemver(VERSION),
    [`${serverRoot}/apps/v8-agent-os-engine/main.py`]: "# fixture", [`${serverRoot}/apps/v8-agent-os-cli/bin/v8os.mjs`]: "// fixture", [`${serverRoot}/SHA256SUMS`]: "fixture",
  });
  const engineIdentity = { schema: 1, profile: "engine", runtimeProfile: "server", startupProfile: "server", version: VERSION, target: "linux-x64", sourceCommit: COMMIT, sourceDirty: false,
    engineDir: "apps/v8-agent-os-engine", python: "apps/v8-agent-os-engine/.python/bin/python3", cli: "apps/v8-agent-os-cli/bin/v8os.mjs" };
  const engine = path.join(input, "server", `V8OS-Engine-${VERSION}-linux-x64.tar.gz`);
  engineTarFixture(f.root, engine, engineIdentity);
  const publicManifest = path.join(input, "server", `V8OS-Engine-${VERSION}-linux-x64.json`);
  json(publicManifest, { schema: 1, profile: "engine", runtimeProfile: "server", startupProfile: "server", version: VERSION, target: "linux-x64", sourceCommit: COMMIT,
    root: `v8os-engine-${VERSION}-linux-x64`, asset: path.basename(engine), sha256: createHash("sha256").update(fs.readFileSync(engine)).digest("hex") });
  fs.mkdirSync(path.join(input, "desktop"), { recursive: true });
  for (const suffix of ["win-x64-setup.exe", "win-arm64-setup.exe", "macos-x64.dmg", "macos-arm64.dmg", "linux-x64.AppImage", "linux-x64.deb", "linux-arm64.AppImage", "linux-arm64.deb"]) {
    fs.writeFileSync(path.join(input, "desktop", `V8-Agent-OS-preview-${VERSION}-${suffix}`), "synthetic desktop asset");
  }
  fs.mkdirSync(path.join(input, "phone/android"), { recursive: true });
  fs.writeFileSync(path.join(input, "phone/android/app-release.apk"), "synthetic android asset");
  tarFixture(f.root, path.join(input, "tui", `V8OS-TUI-${VERSION}.tgz`), tuiEntries());
  assert.doesNotThrow(() => verifyEngineArchiveIdentity(engine, { version: VERSION, target: "linux-x64", sourceCommit: COMMIT }));
  assert.doesNotThrow(() => verifyEngineReleaseAssets({ archive: engine, manifest: publicManifest, version: VERSION, target: "linux-x64", sourceCommit: COMMIT }));
  const mismatchedPublic = path.join(input, "server", "mismatched-profile.json");
  json(mismatchedPublic, { ...JSON.parse(fs.readFileSync(publicManifest, "utf8")), runtimeProfile: "desktop" });
  assert.throws(() => verifyEngineReleaseAssets({ archive: engine, manifest: mismatchedPublic, version: VERSION, target: "linux-x64", sourceCommit: COMMIT }), /public manifest/);
  const result = prepareUnifiedReleaseAssets({ manifestPath: f.manifestPath, inputDir: input, outputDir: path.join(f.root, "out-engine"), sourceCommit: COMMIT });
  assert.ok(result.assets.includes(`V8OS-Engine-${VERSION}-linux-x64.tar.gz`));
  assert.ok(result.assets.includes(`V8OS-Engine-${VERSION}-linux-x64.json`));
  const badManifest = path.join(f.root, "bad-engine.json");
  json(badManifest, { ...JSON.parse(fs.readFileSync(publicManifest, "utf8")), sha256: "0".repeat(64) });
  assert.throws(() => verifyEngineReleaseAssets({ archive: engine, manifest: badManifest, version: VERSION, target: "linux-x64", sourceCommit: COMMIT }), /public manifest/);
  fs.rmSync(publicManifest);
  assert.throws(() => prepareUnifiedReleaseAssets({ manifestPath: f.manifestPath, inputDir: input, outputDir: path.join(f.root, "out-missing-engine-json"), sourceCommit: COMMIT }), /asset and manifest must be published as a pair/);
});

test("real npm pack rebuilds committed source instead of stale dist and installs the matching UI", (t) => {
  const f = packableFixture(t), { source, sourceCommit } = f;
  const file = packTuiRelease({ repoRoot: f.root, outputDir: path.join(f.root, "packed"), sourceCommit });
  const pkg = verifyArchiveIdentity(file, { product: "tui", version: VERSION, sourceCommit });
  assert.equal(pkg.scripts, undefined);
  assert.equal(path.basename(file), `V8OS-TUI-${VERSION}.tgz`);
  const npmCli = [path.join(path.dirname(process.execPath), "node_modules/npm/bin/npm-cli.js"), path.resolve(path.dirname(process.execPath), "../lib/node_modules/npm/bin/npm-cli.js")].find(fs.existsSync);
  const prefix = path.join(f.root, "installed");
  execFileSync(process.execPath, [npmCli, "install", "--prefix", prefix, "--ignore-scripts", "--no-audit", "--no-fund", file], { encoding: "utf8" });
  assert.equal(execFileSync(process.execPath, [path.join(prefix, "node_modules/@v8-agent-os/v8-agent-os/bin/v8os-tui.mjs")], { encoding: "utf8" }).trim(), "current inline command menu");
  assert.match(execFileSync(process.execPath, [path.join(prefix, "node_modules/@v8-agent-os/v8-agent-os/bin/v8os.mjs")], { encoding: "utf8" }), /unified entrypoint/);
  assert.doesNotMatch(execFileSync("tar", ["-tzf", file], { encoding: "utf8" }), /orphan\.js|node_modules/);
  assert.match(fs.readFileSync(path.join(source, "dist/main.js"), "utf8"), /stale fullscreen menu/, "pack must not rewrite the caller's build output");
  assert.match(fs.readFileSync(path.join(source, "node_modules/keep.txt"), "utf8"), /installed toolchain/);
  assert.throws(() => packTuiRelease({ repoRoot: f.root, outputDir: path.join(f.root, "packed"), sourceCommit }), /overwrite/);
  assert.throws(() => packTuiRelease({ repoRoot: f.root, outputDir: path.join(f.root, "wrong-head"), sourceCommit: COMMIT }), /differs from the checkout/);
  fs.writeFileSync(path.join(source, "untracked-source.ts"), "untracked fixture");
  assert.throws(() => packTuiRelease({ repoRoot: f.root, outputDir: path.join(f.root, "candidate"), sourceCommit }), /inputs are modified/);
});

test("failed build and ignored source import never produce a final TUI archive", (t) => {
  for (const [name, extraBuild] of [
    ["failed-build", () => "throw new Error('fixture compile failure');"],
    ["ignored-import", () => "await import('../src/ignored-local.mjs');"],
  ]) {
    const f = packableFixture(t, extraBuild);
    fs.writeFileSync(path.join(f.source, "src/ignored-local.mjs"), "export const uncommitted = true;");
    const outputDir = path.join(f.root, name);
    assert.throws(() => packTuiRelease({ repoRoot: f.root, outputDir, sourceCommit: f.sourceCommit }), /fixture compile failure|ERR_MODULE_NOT_FOUND/);
    assert.equal(fs.existsSync(path.join(outputDir, `V8OS-TUI-${VERSION}.tgz`)), false);
    assert.match(fs.readFileSync(path.join(f.source, "node_modules/keep.txt"), "utf8"), /installed toolchain/);
  }
});

test("source or HEAD changing during build is rejected before the final archive is published", (t) => {
  for (const kind of ["source", "head"]) {
    const f = packableFixture(t, root => kind === "source"
      ? `fs.appendFileSync(${JSON.stringify(path.join(root, "apps/v8-agent-os-tui/src/main.js"))}, '\\n// changed during build');`
      : `import { execFileSync } from 'node:child_process'; execFileSync('git', ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '--allow-empty', '-qm', 'head changed during build'], { cwd: ${JSON.stringify(root)} });`);
    const outputDir = path.join(f.root, "candidate");
    assert.throws(() => packTuiRelease({ repoRoot: f.root, outputDir, sourceCommit: f.sourceCommit }), /inputs are modified|differs from the checkout/);
    assert.equal(fs.existsSync(path.join(outputDir, `V8OS-TUI-${VERSION}.tgz`)), false);
  }
});

test("notes list only enabled Server/TUI products and reject a mismatched manifest", (t) => {
  const f = fixture(t);
  f.manifest.products.server.enabled = f.manifest.products.server.required = true;
  json(f.manifestPath, f.manifest);
  const args = [path.join(ROOT, "scripts/release/generate-release-notes.mjs"), "--manifest", f.manifestPath, "--product", "all", "--version", VERSION, "--channel", "preview"];
  const notes = execFileSync(process.execPath, args, { cwd: f.root, encoding: "utf8" });
  assert.match(notes, new RegExp(`V8OS-TUI-${VERSION.replaceAll('.', '\\.')}\\.tgz`));
  assert.match(notes, /Node\.js 22\+/);
  assert.match(notes, /schema 4/);
  f.manifest.products.tui.enabled = f.manifest.products.tui.required = false;
  f.manifest.products.tui.reason = "not enabled";
  json(f.manifestPath, f.manifest);
  const without = execFileSync(process.execPath, args, { cwd: f.root, encoding: "utf8" });
  assert.doesNotMatch(without, /V8OS-TUI-/);
  assert.match(without, /V8OS-Server-/);
  assert.throws(() => execFileSync(process.execPath, [...args, "--version", "2026.01.01.1"], { cwd: f.root, stdio: "pipe" }), /identity differs/);
});

test("workflows wire required TUI output and both clean Server OS legs without artifact collisions", () => {
  const release = fs.readFileSync(path.join(ROOT, ".github/workflows/release.yml"), "utf8");
  const server = fs.readFileSync(path.join(ROOT, ".github/workflows/server-build.yml"), "utf8");
  const terminal = fs.readFileSync(path.join(ROOT, ".github/workflows/tui-build.yml"), "utf8");
  assert.match(release, /TUI_RESULT: \$\{\{ needs\.tui-build\.result \}\}/);
  assert.match(release, /evaluateReleaseGate\(\{/);
  assert.match(release, /name: v8os-tui-npm\s+path: release-input\/tui/);
  assert.match(release, /--source-commit "\$\{\{ github\.sha \}\}"/);
  assert.match(release, /V8OS-TUI-\*\.tgz/);
  assert.match(release, /const targets = \['linux-x64', 'windows-x64', 'windows-arm64', 'macos-x64', 'macos-arm64'\]/);
  assert.match(release, /verifyEngineReleaseAssets\(\{ archive, manifest, version, target, sourceCommit \}\)/);
  assert.match(server, /ubuntu: \['22\.04', '24\.04'\]/);
  assert.match(server, /matrix\.node == '22' && matrix\.ubuntu == '24\.04'/);
  assert.match(server, /libssl3 libffi8/);
  assert.match(server, /libssl3t64 libffi8/);
  assert.match(terminal, /terminal_pty\.py --node.*--bin "\$installed"/);
  assert.match(terminal, /terminal_pty\.py --node.*--expect-unsupported-node/);
  assert.match(terminal, /if-no-files-found: error/);
  assert.doesNotMatch(terminal, /continue-on-error|\|\| true/);
});

test("the workflow executes its package identity check for the actual hyphenated bin", t => {
  const terminal = fs.readFileSync(path.join(ROOT, ".github/workflows/tui-build.yml"), "utf8");
  const script = terminal.match(/node - "\$\{archives\[0\]\}" <<'NODE'\r?\n([\s\S]*?)\r?\n\s+NODE/)[1];
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "v8-tui-workflow-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const archive = path.join(root, "package.tgz");
  const run = () => execFileSync(process.execPath, ["-", archive], { input: script, encoding: "utf8", stdio: ["pipe", "pipe", "pipe"] });
  tarFixture(root, archive, tuiEntries());
  assert.doesNotThrow(run);
  tarFixture(root, archive, tuiEntries({ ...tuiPackage(), bin: { v8os: "bin/wrong.mjs", "v8os-tui": "bin/v8os-tui.mjs" } }));
  assert.throws(run, /missing unified v8os/);
  tarFixture(root, archive, tuiEntries({ ...tuiPackage(), bin: { v8os: "bin/v8os.mjs" } }));
  assert.throws(run, /missing v8os-tui/);
});
