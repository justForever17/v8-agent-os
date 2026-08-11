const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const {
  RELEASES_API_URL,
  assetNamesFor,
  checkForDesktopUpdate,
  checksumUrlForTag,
  compareReleaseVersions,
  loadReleaseIdentity,
  releaseUrlForTag,
  selectDesktopUpdate,
  toDesktopSemver,
  validateReleaseIdentity,
} = require('../lib/update-check.cjs');

const CURRENT_VERSION = '2026.08.09.3';
const VALID_SHA256 = 'a'.repeat(64);

function textResponse(text, options = {}) {
  const bytes = Buffer.from(text, 'utf8');
  const contentLength = options.contentLength === undefined ? bytes.byteLength : options.contentLength;
  const response = {
    ok: options.ok ?? true,
    status: options.status ?? 200,
    url: options.url || RELEASES_API_URL,
    headers: {
      get(name) {
        return String(name).toLowerCase() === 'content-length' && contentLength !== null
          ? String(contentLength)
          : null;
      },
    },
  };
  if (options.stream === false) {
    response.text = async () => text;
    return response;
  }
  let emitted = false;
  response.body = {
    getReader() {
      return {
        async read() {
          if (emitted) return { done: true, value: undefined };
          emitted = true;
          return { done: false, value: bytes };
        },
        async cancel() {
          options.onCancel?.();
        },
        releaseLock() {},
      };
    },
  };
  return response;
}

function checksumText(assetNames, options = {}) {
  return `${assetNames
    .filter((name) => name !== options.omit)
    .map((name) => `${name === options.invalid ? 'z'.repeat(64) : VALID_SHA256}  ${name}`)
    .join('\n')}\n`;
}

function manifest(version = CURRENT_VERSION, channel = 'preview') {
  return {
    schema: 2,
    release: { version, channel, tag: `v8-os-v${version}` },
    products: {
      desktop: {
        enabled: true,
        targets: {
          'windows-x64': { enabled: true },
          'windows-arm64': { enabled: true },
          'macos-x64': { enabled: true },
          'macos-arm64': { enabled: true },
          'linux-x64': { enabled: true },
          'linux-arm64': { enabled: true },
        },
      },
    },
  };
}

function release(version, options = {}) {
  const platform = options.platform || 'linux';
  const arch = options.arch || 'x64';
  const channel = options.channel || 'preview';
  const assetNames = assetNamesFor(version, channel, platform, arch);
  return {
    tag_name: options.tag || `v8-os-v${version}`,
    draft: options.draft ?? false,
    prerelease: options.prerelease ?? (channel === 'preview'),
    published_at: '2026-08-10T00:00:00Z',
    assets: options.assets || [
      ...assetNames.map((name) => ({ name, state: 'uploaded', size: 1024 })),
      { name: 'SHA256SUMS.txt', state: 'uploaded', size: 128 },
    ],
  };
}

test('schema 2 identity must match the packaged app version and enabled target', () => {
  const identity = validateReleaseIdentity(manifest(), toDesktopSemver(CURRENT_VERSION), 'linux', 'x64');
  assert.deepEqual(identity, {
    version: CURRENT_VERSION,
    channel: 'preview',
    tag: `v8-os-v${CURRENT_VERSION}`,
    target: 'linux-x64',
    platform: 'linux',
    arch: 'x64',
  });
  assert.throws(
    () => validateReleaseIdentity(manifest(), '2026.8.9-2', 'linux', 'x64'),
    (error) => error.code === 'release_identity_mismatch',
  );
  assert.throws(
    () => validateReleaseIdentity(manifest(), toDesktopSemver(CURRENT_VERSION), 'linux', 'ia32'),
    (error) => error.code === 'unsupported_desktop_target',
  );
});

test('release identity loads from the packaged manifest and fails closed when unreadable', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8os-update-identity-'));
  const manifestPath = path.join(root, 'release-manifest.json');
  try {
    fs.writeFileSync(manifestPath, JSON.stringify(manifest()), 'utf8');
    const identity = loadReleaseIdentity(manifestPath, toDesktopSemver(CURRENT_VERSION), 'win32', 'x64');
    assert.equal(identity.target, 'windows-x64');
    assert.throws(
      () => loadReleaseIdentity(path.join(root, 'missing.json'), toDesktopSemver(CURRENT_VERSION), 'win32', 'x64'),
      (error) => error.code === 'release_manifest_unavailable',
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('release comparison is numeric and validates real calendar dates', () => {
  assert.equal(compareReleaseVersions('2026.08.09.10', '2026.08.09.3'), 1);
  assert.equal(compareReleaseVersions('2026.08.10.1', '2026.08.09.99'), 1);
  assert.throws(
    () => compareReleaseVersions('2026.02.30.1', CURRENT_VERSION),
    (error) => error.code === 'invalid_release_version',
  );
});

test('selector accepts only a newer complete unified release in the configured channel', () => {
  const identity = validateReleaseIdentity(manifest(), toDesktopSemver(CURRENT_VERSION), 'linux', 'x64');
  const selected = selectDesktopUpdate([
    release('2026.08.14.1', { draft: true }),
    release('2026.08.13.1', { prerelease: false }),
    release('2026.08.12.1', { tag: 'v8-os-desktop-v2026.08.12.1' }),
    release('2026.08.11.1', { assets: [{ name: 'SHA256SUMS.txt', state: 'uploaded', size: 10 }] }),
    release('2026.08.10.3', {
      assets: [
        { name: 'V8-Agent-OS-preview-2026.08.10.3-linux-x64.AppImage', state: 'uploaded', size: 1024 },
        { name: 'SHA256SUMS.txt', state: 'uploaded', size: 128 },
      ],
    }),
    release('2026.08.10.2'),
    release('2026.08.10.1'),
    release(CURRENT_VERSION),
  ], identity);
  assert.equal(selected.version, '2026.08.10.2');
  assert.equal(selected.releaseUrl, releaseUrlForTag('v8-os-v2026.08.10.2'));
  assert.deepEqual(selected.assetNames, [
    'V8-Agent-OS-preview-2026.08.10.2-linux-x64.AppImage',
    'V8-Agent-OS-preview-2026.08.10.2-linux-x64.deb',
  ]);
});

test('asset matching covers every supported desktop platform and architecture', () => {
  for (const [platform, prefix] of [['win32', 'win'], ['darwin', 'macos'], ['linux', 'linux']]) {
    for (const arch of ['x64', 'arm64']) {
      const names = assetNamesFor('2026.08.10.1', 'preview', platform, arch);
      assert.ok(names.every((name) => name.startsWith('V8-Agent-OS-preview-2026.08.10.1-')));
      assert.ok(names.some((name) => name.includes(`${prefix}-${arch}`)));
    }
  }
});

test('network check validates governed release checksums and never needs a token', async () => {
  const identity = validateReleaseIdentity(manifest(), toDesktopSemver(CURRENT_VERSION), 'linux', 'x64');
  const candidate = release('2026.08.10.1');
  const expectedAssets = assetNamesFor('2026.08.10.1', 'preview', 'linux', 'x64');
  const requests = [];
  const result = await checkForDesktopUpdate({
    identity,
    fetchImpl: async (url, options) => {
      requests.push({ url, options });
      if (url === RELEASES_API_URL) {
        return textResponse(JSON.stringify([candidate]), { url: RELEASES_API_URL });
      }
      assert.equal(url, checksumUrlForTag(candidate.tag_name));
      return textResponse(checksumText(expectedAssets), {
        url: 'https://release-assets.githubusercontent.com/github-production-release-asset/checksum-proof',
      });
    },
  });
  assert.equal(result.state, 'available');
  assert.equal(requests.length, 2);
  assert.equal(requests[0].url, RELEASES_API_URL);
  assert.doesNotMatch(requests[0].url, /\/latest$/);
  assert.equal(requests[0].options.redirect, 'error');
  assert.equal(requests[1].url, checksumUrlForTag(candidate.tag_name));
  assert.equal(requests[1].options.redirect, 'follow');
  for (const request of requests) {
    assert.equal(Object.hasOwn(request.options.headers, 'Authorization'), false);
    assert.equal(request.options.credentials, 'omit');
  }
});

test('streaming release reads cancel immediately when the one MiB limit is exceeded', async () => {
  const identity = validateReleaseIdentity(manifest(), toDesktopSemver(CURRENT_VERSION), 'linux', 'x64');
  let cancelled = false;
  let readCount = 0;
  await assert.rejects(
    checkForDesktopUpdate({
      identity,
      fetchImpl: async () => ({
        ok: true,
        status: 200,
        url: RELEASES_API_URL,
        headers: { get: () => null },
        body: {
          getReader() {
            return {
              async read() {
                readCount += 1;
                return { done: false, value: new Uint8Array((1024 * 1024) + 1) };
              },
              async cancel() {
                cancelled = true;
              },
              releaseLock() {},
            };
          },
        },
      }),
    }),
    (error) => error.code === 'release_response_too_large',
  );
  assert.equal(cancelled, true);
  assert.equal(readCount, 1);
});

test('streaming checksum reads are independently bounded and cancelled', async () => {
  const identity = validateReleaseIdentity(manifest(), toDesktopSemver(CURRENT_VERSION), 'linux', 'x64');
  const candidate = release('2026.08.10.1');
  let checksumCancelled = false;
  await assert.rejects(
    checkForDesktopUpdate({
      identity,
      fetchImpl: async (url) => {
        if (url === RELEASES_API_URL) return textResponse(JSON.stringify([candidate]), { url });
        return {
          ok: true,
          status: 200,
          url,
          headers: { get: () => null },
          body: {
            getReader() {
              return {
                async read() {
                  return { done: false, value: new Uint8Array((64 * 1024) + 1) };
                },
                async cancel() {
                  checksumCancelled = true;
                },
                releaseLock() {},
              };
            },
          },
        };
      },
    }),
    (error) => error.code === 'checksum_response_too_large',
  );
  assert.equal(checksumCancelled, true);
});

test('checksum verification rejects missing and invalid entries for current platform assets', async () => {
  const identity = validateReleaseIdentity(manifest(), toDesktopSemver(CURRENT_VERSION), 'linux', 'x64');
  const candidate = release('2026.08.10.1');
  const expectedAssets = assetNamesFor('2026.08.10.1', 'preview', 'linux', 'x64');
  async function runWithChecksum(contents, responseUrl = checksumUrlForTag(candidate.tag_name)) {
    return checkForDesktopUpdate({
      identity,
      fetchImpl: async (url) => url === RELEASES_API_URL
        ? textResponse(JSON.stringify([candidate]), { url })
        : textResponse(contents, { url: responseUrl }),
    });
  }
  await assert.rejects(
    runWithChecksum(checksumText(expectedAssets, { omit: expectedAssets[1] })),
    (error) => error.code === 'checksum_entry_missing',
  );
  await assert.rejects(
    runWithChecksum(checksumText(expectedAssets, { invalid: expectedAssets[1] })),
    (error) => error.code === 'invalid_checksum_entry',
  );
  await assert.rejects(
    runWithChecksum(checksumText(expectedAssets), 'https://example.com/SHA256SUMS.txt'),
    (error) => error.code === 'checksum_url_not_allowed',
  );
});

test('network, rate limit, malformed response and timeout failures are bounded', async () => {
  const identity = validateReleaseIdentity(manifest(), toDesktopSemver(CURRENT_VERSION), 'linux', 'x64');
  await assert.rejects(
    checkForDesktopUpdate({
      identity,
      fetchImpl: async () => ({ ok: false, status: 429 }),
    }),
    (error) => error.code === 'rate_limited',
  );
  await assert.rejects(
    checkForDesktopUpdate({
      identity,
      fetchImpl: async () => textResponse('{', { stream: false }),
    }),
    (error) => error.code === 'invalid_release_response',
  );
  await assert.rejects(
    checkForDesktopUpdate({
      identity,
      fetchImpl: async () => ({
        ok: true,
        status: 200,
        headers: { get: () => null },
        text: async () => '[]',
      }),
    }),
    (error) => error.code === 'response_body_unavailable',
  );
  await assert.rejects(
    checkForDesktopUpdate({
      identity,
      timeoutMs: 10,
      fetchImpl: (_url, options) => new Promise((_resolve, reject) => {
        options.signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
      }),
    }),
    (error) => error.code === 'timeout',
  );
});
