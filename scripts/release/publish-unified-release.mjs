#!/usr/bin/env node
import { createHash } from 'node:crypto';
import { execFile } from 'node:child_process';
import { createReadStream } from 'node:fs';
import fs from 'node:fs/promises';
import path from 'node:path';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';

const exec = promisify(execFile);
async function digest(filename) {
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(filename)) hash.update(chunk);
  return hash.digest('hex');
}

// Consume only the output of prepare-unified-release-assets. Never replace a
// published asset: its name, size and digest must all agree before any upload.
export async function localAssets(directory) {
  const sums = await fs.readFile(path.join(directory, 'SHA256SUMS.txt'), 'utf8');
  const assets = [];
  const seen = new Set();
  for (const line of sums.trim().split(/\r?\n/)) {
    const match = /^([a-f0-9]{64})  ([A-Za-z0-9][A-Za-z0-9._-]*)$/.exec(line);
    if (!match || match[2] === 'SHA256SUMS.txt' || seen.has(match[2])) throw new Error('Invalid or duplicate checksum entry');
    const [, sha256, name] = match;
    if (!/^(V8-Agent-OS-|V8OS-Phone-|V8OS-Server-|V8OS-TUI-)/.test(name)) throw new Error(`Unexpected public asset: ${name}`);
    seen.add(name);
    assets.push({ name, sha256 });
  }
  assets.push({ name: 'SHA256SUMS.txt', sha256: await digest(path.join(directory, 'SHA256SUMS.txt')) });
  const entries = await fs.readdir(directory);
  if (entries.length !== assets.length || entries.some(name => !assets.some(asset => asset.name === name))) throw new Error('Release directory differs from checksum inventory');
  for (const asset of assets) {
    asset.file = path.resolve(directory, asset.name);
    const stat = await fs.lstat(asset.file);
    if (!stat.isFile() || stat.isSymbolicLink()) throw new Error(`Not a regular release asset: ${asset.name}`);
    asset.size = stat.size;
    if (await digest(asset.file) !== asset.sha256) throw new Error(`Local checksum mismatch: ${asset.name}`);
  }
  return assets;
}

function reconcile(release, expected) {
  const found = new Map();
  for (const remote of release.assets) {
    const asset = expected.find(item => item.name === remote.name);
    if (!asset || found.has(remote.name) || remote.state !== 'uploaded' || remote.size !== asset.size || remote.digest !== `sha256:${asset.sha256}`) {
      throw new Error(`Remote asset conflict; nothing will be overwritten: ${remote.name}`);
    }
    found.set(remote.name, remote);
  }
  return found;
}

export async function publishAssets({ assets, api, attempts = 3, wait = ms => new Promise(resolve => setTimeout(resolve, ms)), progress = () => {} }) {
  await api.verifyTag();
  let release = await api.read();
  if (!release) { await api.createDraft(); release = await api.read(); }
  if (!release) throw new Error('Release creation was not confirmed');
  // Check every existing asset before adding any missing file.
  let present = reconcile(release, assets);
  for (const asset of assets) {
    if (present.has(asset.name)) { progress(`Verified existing ${asset.name}`); continue; }
    let confirmed = false;
    for (let attempt = 1; attempt <= attempts; attempt++) {
      progress(`Uploading ${asset.name} (${attempt}/${attempts})`);
      let uploadError;
      try { await api.upload(asset); } catch (error) { uploadError = error; }
      // A lost response can follow a completed upload. Read authority before
      // retrying; neither --clobber nor asset deletion is allowed here.
      release = await api.read();
      if (!release) throw new Error('Release disappeared during upload');
      present = reconcile(release, assets);
      if (present.has(asset.name)) { confirmed = true; break; }
      if (attempt === attempts) throw new Error(`Upload not confirmed: ${asset.name}`, { cause: uploadError });
      await wait(1000 * attempt);
    }
    if (!confirmed) throw new Error(`Missing asset: ${asset.name}`);
  }
  await api.verifyTag();
  release = await api.read();
  if (!release || reconcile(release, assets).size !== assets.length) throw new Error('Incomplete release');
  if (release.isDraft) await api.publishDraft();
  release = await api.read();
  if (!release || release.isDraft || reconcile(release, assets).size !== assets.length) throw new Error('Publication was not confirmed');
  return { assets: assets.length, url: release.url, verified: true };
}

export function githubApi({ repo, tag, sourceCommit, notes, prerelease, gh }) {
  const call = gh || (async args => (await exec('gh', args, { encoding: 'utf8', windowsHide: true, timeout: 20 * 60 * 1000, maxBuffer: 4 * 1024 * 1024 })).stdout);
  return {
    async verifyTag() {
      let object = JSON.parse(await call(['api', `repos/${repo}/git/ref/tags/${tag}`])).object;
      for (let depth = 0; object.type === 'tag' && depth < 4; depth++) object = JSON.parse(await call(['api', `repos/${repo}/git/tags/${object.sha}`])).object;
      if (object.type !== 'commit' || object.sha !== sourceCommit) throw new Error('Remote tag does not match the validated source commit');
    },
    async read() {
      try {
        const release = JSON.parse(await call(['release', 'view', tag, '--repo', repo, '--json', 'tagName,isDraft,isPrerelease,url,assets']));
        if (release.tagName !== tag || release.isPrerelease !== prerelease) throw new Error('Release identity/channel mismatch');
        return release;
      } catch (error) {
        if (error.code && /release not found|HTTP 404/.test(error.stderr || '')) return null;
        throw error;
      }
    },
    createDraft: () => call(['release', 'create', tag, '--repo', repo, '--verify-tag', '--draft', '--title', tag, '--notes-file', notes, ...(prerelease ? ['--prerelease'] : [])]),
    upload: asset => call(['release', 'upload', tag, asset.file, '--repo', repo]),
    publishDraft: () => call(['release', 'edit', tag, '--repo', repo, '--draft=false', `--latest=${!prerelease}`]),
  };
}

async function main() {
  const values = process.argv.slice(2), args = {};
  for (let index = 0; index < values.length; index += 2) {
    if (!values[index]?.startsWith('--') || !values[index + 1]) throw new Error('Expected --name value arguments');
    args[values[index].slice(2)] = values[index + 1];
  }
  if (!/^[\w.-]+\/[\w.-]+$/.test(args.repo || '') || !/^v8-os(?:-desktop|-phone)?-v\d{4}\.\d{2}\.\d{2}\.\d+$/.test(args.tag || '') || !/^[a-f0-9]{40}$/.test(args['source-commit'] || '') || !['true', 'false'].includes(args.prerelease) || !args.notes || !args.directory) throw new Error('Missing or invalid release identity/paths');
  const assets = await localAssets(args.directory);
  const api = githubApi({ repo: args.repo, tag: args.tag, sourceCommit: args['source-commit'], notes: args.notes, prerelease: args.prerelease === 'true' });
  console.log(JSON.stringify(await publishAssets({ assets, api, progress: console.log })));
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main().catch(error => { console.error(error.message); process.exitCode = 1; });
