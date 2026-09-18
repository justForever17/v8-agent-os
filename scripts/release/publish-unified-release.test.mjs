import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { localAssets, publishAssets, githubApi } from './publish-unified-release.mjs';

const assets = ['a', 'b'].map((name, index) => ({ name, size: index + 1, sha256: String(index).repeat(64), file: `/fixture/${name}` }));
const remote = asset => ({ ...asset, state: 'uploaded', digest: `sha256:${asset.sha256}` });
function fixture(initial = []) {
  let release = { assets: initial, isDraft: false, url: 'fixture' };
  const writes = [];
  const api = {
    verifyTag: async () => {}, read: async () => structuredClone(release),
    createDraft: async () => { writes.push('create'); release = { assets: [], isDraft: true, url: 'fixture' }; },
    upload: async asset => { writes.push(asset.name); release.assets.push(remote(asset)); },
    publishDraft: async () => { writes.push('publish'); release.isDraft = false; },
  };
  return { api, writes, release: value => { release = value; } };
}
test('partial published release skips verified assets and adds only missing files', async () => {
  const f = fixture([remote(assets[0])]);
  assert.equal((await publishAssets({ assets, api: f.api })).verified, true);
  assert.deepEqual(f.writes, ['b']);
});
test('lost upload response is reconciled without duplicate upload or overwrite', async () => {
  const f = fixture(), upload = f.api.upload;
  f.api.upload = async asset => { await upload(asset); throw new Error('connection lost after persistence'); };
  await publishAssets({ assets, api: f.api });
  assert.deepEqual(f.writes, ['a', 'b']);
});
test('failed upload retries finitely and leaves a new release in draft', async () => {
  const f = fixture(); f.release(null); let calls = 0;
  f.api.upload = async () => { calls++; throw new Error('unavailable'); };
  await assert.rejects(publishAssets({ assets, api: f.api, wait: async () => {} }), /Upload not confirmed/);
  assert.equal(calls, 3); assert.deepEqual(f.writes, ['create']);
  assert.equal((await f.api.read()).isDraft, true);
});
test('new release is published only after all exact assets are confirmed', async () => {
  const f = fixture(); f.release(null);
  await publishAssets({ assets, api: f.api });
  assert.deepEqual(f.writes, ['create', 'a', 'b', 'publish']);
});
for (const [label, altered] of [['digest', { digest: `sha256:${'f'.repeat(64)}` }], ['size', { size: 99 }], ['starter', { state: 'starter' }], ['unexpected', { name: 'extra' }]]) {
  test(`existing ${label} conflict stops before any missing upload`, async () => {
    const f = fixture([{ ...remote(assets[1]), ...altered }]);
    await assert.rejects(publishAssets({ assets, api: f.api }), /Remote asset conflict/);
    assert.deepEqual(f.writes, []);
  });
}
test('moved remote tag refuses draft creation or upload', async () => {
  const f = fixture(); f.release(null); f.api.verifyTag = async () => { throw new Error('tag mismatch'); };
  await assert.rejects(publishAssets({ assets, api: f.api }), /tag mismatch/);
  assert.deepEqual(f.writes, []);
});
test('adapter never requests clobber and resolves annotated tags to the commit', async () => {
  const calls = [], sha = 'a'.repeat(40);
  const api = githubApi({ repo: 'org/repo', tag: 'v8-os-v2026.09.17.3', sourceCommit: sha, notes: 'notes.md', prerelease: true, gh: async args => {
    calls.push(args);
    if (args[0] === 'api') return JSON.stringify({ object: { type: args[1].includes('/ref/') ? 'tag' : 'commit', sha } });
    return '';
  } });
  await api.verifyTag(); await api.upload(assets[0]); await api.createDraft(); await api.publishDraft();
  assert.equal(calls.filter(args => args[0] === 'api').length, 2);
  assert.ok(calls.flat().includes('--verify-tag')); assert.ok(calls.flat().includes('--draft'));
  assert.ok(!calls.flat().includes('--clobber')); assert.ok(!calls.flat().includes('delete'));
});
test('prepared checksum inventory refuses changed bytes, extra files and path escape', async t => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'v8-publish-'));
  t.after(() => fs.rm(dir, { recursive: true, force: true }));
  const name = 'V8OS-TUI-2026.09.17.3.tgz', content = 'fixture';
  const sha = createHash('sha256').update(content).digest('hex');
  await fs.writeFile(path.join(dir, name), content);
  await fs.writeFile(path.join(dir, 'SHA256SUMS.txt'), `${sha}  ${name}\n`);
  assert.equal((await localAssets(dir)).length, 2);
  await fs.writeFile(path.join(dir, name), 'wrong');
  await assert.rejects(localAssets(dir), /checksum mismatch/);
  await fs.writeFile(path.join(dir, name), content); await fs.writeFile(path.join(dir, 'extra'), 'bad');
  await assert.rejects(localAssets(dir), /differs/);
  await fs.writeFile(path.join(dir, 'SHA256SUMS.txt'), `${sha}  ../${name}\n`);
  await assert.rejects(localAssets(dir), /Invalid/);
});
