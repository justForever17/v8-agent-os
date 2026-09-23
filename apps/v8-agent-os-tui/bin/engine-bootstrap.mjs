import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { fileURLToPath } from 'node:url';
import { list, extract } from 'tar';

const packageRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const packageJson = JSON.parse(fs.readFileSync(path.join(packageRoot, 'package.json'), 'utf8'));
const ENGINE_DIR = 'apps/v8-agent-os-engine';
const CLI_FILE = 'apps/v8-agent-os-cli/bin/v8os.mjs';
const MAX_ARCHIVE_BYTES = 2 * 1024 ** 3;
const readJson = filename => JSON.parse(fs.readFileSync(filename, 'utf8').replace(/^\uFEFF/, ''));
const optionalJson = filename => { try { return readJson(filename); } catch (e) { if (e.code === 'ENOENT') return null; throw e; } };
export const stateRoot = () => path.resolve(process.env.V8_AGENT_OS_HOME || path.join(os.homedir(), '.v8-agent-os'));
const runtimeRoot = () => path.resolve(process.env.V8OS_ENGINE_RUNTIME_ROOT || path.join(stateRoot(), 'runtime', 'engine'));

export function releaseVersion(value = process.env.V8OS_RELEASE_VERSION || packageJson.v8Release?.version || packageJson.version) {
  const match = /^(20\d{2})\.(\d{1,2})\.(\d{1,2})[.-](\d{1,2})$/.exec(value);
  if (!match) throw new Error('Invalid V8OS release version');
  const [, year, month, day, build] = match;
  const date = new Date(`${year}-${month.padStart(2, '0')}-${day.padStart(2, '0')}T00:00:00Z`);
  if (date.getUTCMonth() + 1 !== Number(month) || date.getUTCDate() !== Number(day) || Number(build) < 1) throw new Error('Invalid V8OS release date');
  return `${year}.${month.padStart(2, '0')}.${day.padStart(2, '0')}.${Number(build)}`;
}

export function targetForPlatform(platform = process.platform, arch = process.arch,
  glibc = platform === process.platform && platform === 'linux' ? process.report.getReport().header.glibcVersionRuntime : undefined) {
  if (platform === 'linux' && arch === 'x64') {
    if (platform === process.platform && !glibc) throw new Error('Portable Engine requires glibc Linux; musl/Alpine is not supported');
    if (glibc && (Number(glibc.split('.')[0]) < 2 || (Number(glibc.split('.')[0]) === 2 && Number(glibc.split('.')[1]) < 35))) throw new Error('Portable Engine requires glibc 2.35+ (Ubuntu 22.04/24.04 x64)');
    return 'linux-x64';
  }
  if (platform === 'win32' && ['x64', 'arm64'].includes(arch)) return `windows-${arch}`;
  if (platform === 'darwin' && ['x64', 'arm64'].includes(arch)) return `macos-${arch}`;
  throw new Error(`Portable Engine is unavailable for ${platform}/${arch}; supported targets are Linux glibc 2.35+ x64, Windows x64/arm64 and macOS x64/arm64.`);
}

function pythonPathForTarget(target) {
  return target.startsWith('windows-')
    ? `${ENGINE_DIR}/.python/python.exe`
    : `${ENGINE_DIR}/.python/bin/python3`;
}

function contained(root, relative) {
  if (typeof relative !== 'string' || !relative || /[\\:\x00-\x1f\x7f]/u.test(relative) || path.posix.isAbsolute(relative) || relative.split('/').includes('..')) throw new Error('Unsafe Engine asset path');
  const result = path.resolve(root, relative);
  if (!result.startsWith(path.resolve(root) + path.sep)) throw new Error('Engine path escaped its runtime');
  return result;
}

export function validateManifest(manifest, { version, target, sourceCommit } = {}, downloaded = false) {
  if (manifest?.schema !== 1 || manifest.profile !== 'engine' || manifest.version !== releaseVersion(manifest.version)
      || !['linux-x64', 'windows-x64', 'windows-arm64', 'macos-x64', 'macos-arm64'].includes(manifest.target)
      || !/^[a-f0-9]{40}$/.test(manifest.sourceCommit || '')
      || (version && manifest.version !== version) || (target && manifest.target !== target)
      || (sourceCommit && manifest.sourceCommit !== sourceCommit)) throw new Error('Engine manifest identity mismatch');
  if (downloaded) {
    if (manifest.root !== `v8os-engine-${manifest.version}-${manifest.target}`
        || manifest.asset !== `V8OS-Engine-${manifest.version}-${manifest.target}.tar.gz`
        || !/^[a-f0-9]{64}$/.test(manifest.sha256 || '')) throw new Error('Invalid Engine archive manifest');
  } else if (manifest.sourceDirty !== false || manifest.engineDir !== ENGINE_DIR || manifest.cli !== CLI_FILE || manifest.python !== pythonPathForTarget(manifest.target)) {
    throw new Error('Invalid portable Engine entrypoints');
  }
  return manifest;
}

function inspectRuntime(root, expected = {}) {
  const manifest = validateManifest(readJson(path.join(root, 'engine-manifest.json')), expected);
  for (const relative of [`${manifest.engineDir}/main.py`, manifest.python, manifest.cli]) {
    const entry = contained(root, relative);
    if (!fs.statSync(entry).isFile() || !fs.realpathSync(entry).startsWith(fs.realpathSync(root) + path.sep)) throw new Error('Engine entrypoint is missing or outside the runtime');
  }
  if (process.platform !== 'win32') fs.accessSync(contained(root, manifest.python), fs.constants.X_OK);
  return { root: path.resolve(root), manifest };
}

export function installedRuntime({ version = releaseVersion(), target } = {}) {
  const explicit = process.env.V8OS_ENGINE_RUNTIME_DIR;
  if (explicit) return inspectRuntime(path.resolve(explicit)).root;
  const destination = path.join(runtimeRoot(), version, target || targetForPlatform());
  if (!fs.existsSync(destination)) return '';
  const result = inspectRuntime(destination, { version, target });
  const receipt = readJson(path.join(destination, '.installed-receipt.json'));
  if (receipt.version !== version || receipt.sourceCommit !== result.manifest.sourceCommit || !/^[a-f0-9]{64}$/.test(receipt.sha256 || '')) throw new Error('Engine installation receipt mismatch');
  return destination;
}

function assetUrl(version, name) {
  return `https://github.com/justForever17/v8-agent-os/releases/download/v8-os-v${version}/${name}`;
}

function sourceUrl(source) {
  const url = new URL(source);
  if (url.username || url.password || (url.protocol !== 'https:' && url.protocol !== 'file:' && !(url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)))) throw new Error('Engine source requires HTTPS, a local file, or a loopback test server');
  return url;
}

async function readManifest(source, signal) {
  signal.throwIfAborted();
  const url = sourceUrl(source);
  if (url.protocol === 'file:') return readJson(fileURLToPath(url));
  const response = await fetch(url, { signal: AbortSignal.any([signal, AbortSignal.timeout(20_000)]), headers: { accept: 'application/json' } });
  if (!response.ok) throw new Error(`Engine manifest download failed (HTTP ${response.status})`);
  const text = await response.text();
  if (text.length > 64 * 1024) throw new Error('Engine manifest is too large');
  return JSON.parse(text);
}

async function download(source, filename, signal) {
  const local = process.env.V8OS_ENGINE_ARCHIVE;
  const url = local ? null : sourceUrl(source);
  let body;
  if (local || url.protocol === 'file:') body = fs.createReadStream(local || fileURLToPath(url));
  else {
    const response = await fetch(url, { signal, redirect: 'follow' });
    if (!response.ok || !response.body) throw new Error(`Engine download failed (HTTP ${response.status})`);
    if (Number(response.headers.get('content-length')) > MAX_ARCHIVE_BYTES) throw new Error('Engine archive exceeds the download limit');
    body = Readable.fromWeb(response.body);
  }
  const hash = createHash('sha256'); let bytes = 0;
  await pipeline(body, async function* (chunks) {
    for await (const chunk of chunks) {
      bytes += chunk.length;
      if (bytes > MAX_ARCHIVE_BYTES) throw new Error('Engine archive exceeds the download limit');
      hash.update(chunk); yield chunk;
    }
  }, fs.createWriteStream(filename, { flags: 'wx', mode: 0o600 }), { signal });
  return hash.digest('hex');
}

export async function extractEngineArchive(archive, destination, root) {
  const names = new Set(); let size = 0; let invalid = '';
  await list({ file: archive, strict: true, onReadEntry(entry) {
    const name = entry.path.replace(/\/$/, '');
    if (name !== root && !name.startsWith(root + '/')) invalid ||= 'Unexpected Engine archive root';
    try { contained(destination, name); } catch { invalid ||= 'Unsafe Engine archive path'; }
    if (names.has(name)) invalid ||= 'Duplicate Engine archive member';
    names.add(name);
    if (!['File', 'Directory', 'SymbolicLink', 'Link'].includes(entry.type)) invalid ||= 'Unsupported Engine archive member type';
    if (entry.linkpath) {
      const link = entry.linkpath;
      const resolved = path.posix.normalize(entry.type === 'Link' ? link : path.posix.join(path.posix.dirname(name), link));
      if (/[\\:\x00-\x1f\x7f]/u.test(link) || path.posix.isAbsolute(link) || !resolved.startsWith(root + '/')) invalid ||= 'Engine archive link escapes runtime';
    }
    size += entry.size || 0;
    if (size > 8 * 1024 ** 3 || names.size > 150_000) invalid ||= 'Engine archive exceeds extraction limits';
  } });
  if (invalid) throw new Error(invalid);
  fs.mkdirSync(destination, { recursive: true, mode: 0o700 });
  await extract({ file: archive, cwd: destination, strict: true, preserveOwner: false, chmod: true });
}

export async function installEngine({ version = releaseVersion(), target = targetForPlatform(), signal = new AbortController().signal, progress = () => {} } = {}) {
  const existing = installedRuntime({ version, target });
  if (existing) return { root: existing, installed: false };
  progress('Downloading Engine manifest / 正在获取引擎清单');
  const remote = validateManifest(await readManifest(process.env.V8OS_ENGINE_MANIFEST_URL || assetUrl(version, `V8OS-Engine-${version}-${target}.json`), signal), {
    version, target, sourceCommit: packageJson.v8Release?.sourceCommit,
  }, true);
  const destination = path.join(runtimeRoot(), version, target);
  fs.mkdirSync(path.dirname(destination), { recursive: true, mode: 0o700 });
  const temporary = fs.mkdtempSync(path.join(path.dirname(destination), '.staging-'));
  try {
    const archive = path.join(temporary, 'engine.tar.gz');
    progress('Downloading Engine / 正在下载引擎');
    const digest = await download(remote.assetUrl || assetUrl(version, remote.asset), archive, AbortSignal.any([signal, AbortSignal.timeout(10 * 60_000)]));
    if (digest !== remote.sha256) throw new Error('Engine archive SHA-256 mismatch; installation was not changed');
    signal.throwIfAborted(); progress('Verifying and extracting Engine / 正在校验并解压引擎');
    const extracted = path.join(temporary, 'extract');
    await extractEngineArchive(archive, extracted, remote.root);
    signal.throwIfAborted();
    const candidate = contained(extracted, remote.root);
    inspectRuntime(candidate, remote);
    fs.writeFileSync(path.join(candidate, '.installed-receipt.json'), JSON.stringify({ schema: 1, version, target, sha256: digest, sourceCommit: remote.sourceCommit }) + '\n', { flag: 'wx', mode: 0o600 });
    // Version directories are immutable. Concurrent first starts can stage in
    // parallel, but only one atomic rename can publish the complete directory.
    try { fs.renameSync(candidate, destination); }
    catch (error) {
      if (!['EEXIST', 'ENOTEMPTY', 'EPERM'].includes(error.code)) throw error;
      const winner = installedRuntime({ version, target });
      if (!winner || readJson(path.join(winner, '.installed-receipt.json')).sha256 !== digest) throw error;
      return { root: winner, installed: false };
    }
    return { root: destination, installed: true };
  } finally { fs.rmSync(temporary, { recursive: true, force: true }); }
}

function recordedRoot({ aliveOnly = false } = {}) {
  const records = optionalJson(path.join(stateRoot(), 'runtime', 'cli', 'processes.json'));
  const cwd = records?.processes?.engine?.cwd;
  if (aliveOnly) {
    const pid = Number(records?.processes?.engine?.pid);
    if (!Number.isInteger(pid) || pid <= 0) return '';
    try { process.kill(pid, 0); } catch (error) { if (error.code !== 'EPERM') return ''; }
  }
  // This is a discovery hint, never proof of process ownership. Core control
  // still verifies PID, creation token, command and instance before use.
  if (cwd && fs.existsSync(path.join(cwd, 'main.py'))) return path.resolve(cwd, '..', '..');
  return '';
}

export function rememberedDesktopRuntime() {
  const records = optionalJson(path.join(stateRoot(), 'runtime', 'cli', 'processes.json'));
  const root = records?.repoRoot;
  // Stopping removes the process receipt, not the recorded desktop installation.
  // Portable version roots deliberately do not use this fallback: npm upgrades
  // must select the current exact version after the old daemon has stopped.
  if (typeof root !== 'string' || !path.isAbsolute(root)
      || fs.existsSync(path.join(root, 'engine-manifest.json'))
      || fs.existsSync(path.join(root, 'server-manifest.json'))
      || !fs.existsSync(path.join(root, ENGINE_DIR, 'main.py'))
      || !fs.existsSync(path.join(root, CLI_FILE))
      || !fs.existsSync(path.join(root, 'apps/v8-agent-os-web'))) return '';
  return root;
}

function configureRuntime(root) {
  process.env.V8_REPO_ROOT = root;
  process.env.V8_AGENT_OS_REPO_ROOT = root;
  process.env.V8_ENGINE_DIR = path.join(root, ENGINE_DIR);
  const manifest = optionalJson(path.join(root, 'engine-manifest.json'));
  if (manifest) {
    inspectRuntime(root);
    process.env.V8_ENGINE_PYTHON = contained(root, manifest.python);
    const runtimeBin = path.dirname(process.env.V8_ENGINE_PYTHON);
    process.env.PATH = [runtimeBin, path.dirname(process.execPath), ...String(process.env.PATH || '').split(path.delimiter)]
      .filter((entry, index, entries) => entry && entries.indexOf(entry) === index).join(path.delimiter);
    delete process.env.PYTHONHOME;
    delete process.env.PYTHONPATH;
    process.env.ENGINE_INSTALL_PROFILE = manifest.runtimeProfile || 'desktop';
    process.env.ENGINE_STARTUP_PROFILE = manifest.startupProfile || manifest.runtimeProfile || 'desktop';
    process.env.ENGINE_RELOAD = '0';
    if (!process.env.CREDENTIALS_DIRECTORY) process.env.V8_AGENT_OS_CREDENTIAL_KEY_FILE ||= path.join(stateRoot(), 'credentials', 'v8-agent-os-credential-key');
  }
  return manifest;
}

async function localControl(root) {
  if (root) configureRuntime(root);
  return import(`../dist/core-control.mjs?runtime=${encodeURIComponent(root || '')}`);
}

async function serviceReceipt() {
  const { discoverServerServiceReceipt } = await import('../dist/core-control.mjs?discovery');
  return discoverServerServiceReceipt();
}

export async function startEngine({ install = true, signal = new AbortController().signal, progress = () => {} } = {}) {
  const service = await serviceReceipt();
  let root = service?.bundleRoot || recordedRoot({ aliveOnly: true });
  if (root && !service) {
    const previous = await localControl(root);
    const status = (await previous.statusCoreComponents(['engine']))[0];
    if (!status?.managed) {
      if (status?.pidAlive) throw new Error('Engine process identity is unverified; use v8os doctor before retrying');
      root = '';
    }
  }
  if (!root) root = rememberedDesktopRuntime() || installedRuntime();
  if (!root && install) root = (await installEngine({ signal, progress })).root;
  if (!root) throw new Error('Engine is not installed. Run v8os install or configure V8OS_ENGINE_RUNTIME_DIR.');
  const core = await localControl(root);
  const origin = core.engineTargetOrigin();
  if (!['localhost', '127.0.0.1', '[::1]'].includes(new URL(origin).hostname)) throw new Error('v8os start requires a local Engine target; local credentials were not sent');
  if (!service && process.env.ENGINE_INSTALL_PROFILE === 'server' && process.env.V8_AGENT_OS_CREDENTIAL_KEY_FILE && !fs.existsSync(process.env.V8_AGENT_OS_CREDENTIAL_KEY_FILE)) {
    try { core.initializeServerCredentials(); }
    catch (error) { if (!fs.existsSync(process.env.V8_AGENT_OS_CREDENTIAL_KEY_FILE)) throw error; }
  }
  progress('Starting Engine / 正在启动引擎');
  signal.throwIfAborted();
  const { results } = await core.startCoreComponentsWithRuntimePorts(['engine'], { mode: 'start' });
  const result = results.find(item => item.id === 'engine');
  if (!['started', 'already_running'].includes(result?.status)) throw new Error(`Engine start failed: ${result?.status || 'no_receipt'}; see v8os logs`);
  try { await core.waitForCoreReadiness({ signal }); }
  catch (error) {
    if (result.status === 'started' && result.recordIdentity) {
      await core.stopCoreComponents(['engine'], { expectedIdentities: { engine: result.recordIdentity } });
    }
    throw error;
  }
  return { ...result, runtimeRoot: root, baseUrl: origin };
}

export async function statusEngine() {
  let root = (await serviceReceipt())?.bundleRoot || recordedRoot() || rememberedDesktopRuntime();
  if (!root) {
    try { root = installedRuntime(); }
    catch (error) { if (process.platform === 'linux' || process.env.V8OS_ENGINE_RUNTIME_DIR) throw error; }
  }
  if (!root) return { status: 'not_installed', platform: `${process.platform}-${process.arch}`, runtimeRoot: null };
  const core = await localControl(root);
  const result = (await core.statusCoreComponents(['engine']))[0];
  return { ...result, status: result?.managed && result?.pidAlive ? 'running' : result?.pidAlive ? 'identity_unverified' : 'stopped', runtimeRoot: root, baseUrl: core.engineTargetOrigin() };
}

export async function stopEngine() {
  let root = (await serviceReceipt())?.bundleRoot || recordedRoot() || rememberedDesktopRuntime();
  if (!root) { try { root = installedRuntime(); } catch (error) { if (process.platform === 'linux' || process.env.V8OS_ENGINE_RUNTIME_DIR) throw error; } }
  if (!root) return { id: 'engine', status: 'not_managed' };
  const core = await localControl(root);
  return (await core.stopCoreComponents(['engine']))[0];
}

export async function runEngineCli(args, options = {}) {
  const service = await serviceReceipt();
  let root = service?.bundleRoot || recordedRoot() || rememberedDesktopRuntime();
  if (!root) { try { root = installedRuntime(); } catch (error) { if (process.platform === 'linux' || process.env.V8OS_ENGINE_RUNTIME_DIR) throw error; } }
  const cliArgs = [...args];
  const serviceMutation = cliArgs[0] === 'service' && !cliArgs.includes('--help') && !cliArgs.includes('-h');
  if (serviceMutation && ['install', 'upgrade'].includes(cliArgs[1]) && !cliArgs.includes('--bundle')) {
    const bundle = (await installEngine(options)).root;
    cliArgs.push('--bundle', bundle);
    root = bundle;
  }
  if (root) configureRuntime(root);
  if (serviceMutation && cliArgs[1] === 'install' && !service
      && process.env.V8_AGENT_OS_CREDENTIAL_KEY_FILE && !fs.existsSync(process.env.V8_AGENT_OS_CREDENTIAL_KEY_FILE)) {
    const core = await localControl(root);
    core.initializeServerCredentials();
  }
  const { main } = await import(`../dist/cli.mjs?runtime=${encodeURIComponent(root || '')}`);
  await main(cliArgs);
  return process.exitCode || 0;
}
