const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('typescript');

function profileCache(readProfile) {
  const filename = path.resolve(__dirname, '../src/hooks/use-client-profile.ts');
  const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }, fileName: filename,
  }).outputText;
  let now = 10000;
  const context = {
    module: { exports: {} }, exports: {}, Date: { now: () => now },
    window: { dispatchEvent() {} }, CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
    require(name) {
      if (name === '@/lib/actions/user.actions') return { getUserProfile: readProfile };
      if (['react', 'next-auth/react', '@/lib/personalization'].includes(name)) return {};
      throw new Error(`Unexpected boundary: ${name}`);
    },
  };
  vm.runInNewContext(compiled + '\nmodule.exports = { loadSharedProfile, emitProfileUpdate, resetSharedProfile };', context);
  return { ...context.module.exports, advance(ms) { now += ms; } };
}

test('a delayed profile response cannot undo a newly saved video background', async () => {
  let resolve;
  const cache = profileCache(() => new Promise((done) => { resolve = done; }));
  const pending = cache.loadSharedProfile();
  const saved = { name: 'fixture', appearance: { lightBackgroundMedia: '/user-assets/background/new.mp4', lightBackgroundEnabled: true } };
  cache.emitProfileUpdate(saved);
  resolve({ success: true, user: { name: 'fixture', appearance: {} } });
  assert.equal(await pending, saved);
  assert.equal(await cache.loadSharedProfile(), saved);
});

test('a temporary profile read failure retains the last confirmed appearance', async () => {
  const cache = profileCache(async () => ({ success: false, error: 'temporary fixture failure' }));
  const saved = { name: 'fixture', appearance: { lightBackgroundEnabled: true } };
  cache.emitProfileUpdate(saved);
  cache.advance(4000);
  assert.equal(await cache.loadSharedProfile(), saved);
});

test('a first profile failure is unknown and is not cached as an empty profile', async () => {
  let reads = 0;
  const saved = { name: 'fixture', appearance: {} };
  const cache = profileCache(async () => ++reads === 1 ? { success: false } : { success: true, user: saved });
  assert.equal(await cache.loadSharedProfile(), undefined);
  assert.equal(await cache.loadSharedProfile(), saved);
  assert.equal(reads, 2);
});

test('sign-out invalidates an outstanding request instead of restoring the previous profile', async () => {
  let resolve;
  const cache = profileCache(() => new Promise((done) => { resolve = done; }));
  const pending = cache.loadSharedProfile();
  cache.resetSharedProfile();
  resolve({ success: true, user: { name: 'previous fixture owner' } });
  assert.equal(await pending, undefined);
});
