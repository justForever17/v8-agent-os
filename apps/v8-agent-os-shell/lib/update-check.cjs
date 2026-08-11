const fs = require('node:fs');

const RELEASES_API_URL = 'https://api.github.com/repos/justForever17/v8-agent-os/releases?per_page=20';
const RELEASE_WEB_BASE_URL = 'https://github.com/justForever17/v8-agent-os/releases/tag/';
const RELEASE_DOWNLOAD_BASE_URL = 'https://github.com/justForever17/v8-agent-os/releases/download/';
const DEFAULT_UPDATE_TIMEOUT_MS = 7000;
const MAX_RELEASE_RESPONSE_BYTES = 1024 * 1024;
const MAX_CHECKSUM_RESPONSE_BYTES = 64 * 1024;
const VERSION_RE = /^20\d{2}\.(?:0[1-9]|1[0-2])\.(?:0[1-9]|[12]\d|3[01])\.(?:[1-9]|[1-9]\d)$/;
const UNIFIED_TAG_RE = /^v8-os-v(20\d{2}\.(?:0[1-9]|1[0-2])\.(?:0[1-9]|[12]\d|3[01])\.(?:[1-9]|[1-9]\d))$/;
const SHA256_RE = /^[a-f0-9]{64}$/i;
const CHECKSUM_REDIRECT_HOSTS = new Set(['release-assets.githubusercontent.com', 'objects.githubusercontent.com']);

class UpdateCheckError extends Error {
  constructor(code, message = code) {
    super(message);
    this.name = 'UpdateCheckError';
    this.code = code;
  }
}

function isValidReleaseVersion(version) {
  if (!VERSION_RE.test(String(version || ''))) return false;
  const [year, month, day] = version.split('.').map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCFullYear() === year
    && date.getUTCMonth() === month - 1
    && date.getUTCDate() === day;
}

function compareReleaseVersions(left, right) {
  if (!isValidReleaseVersion(left) || !isValidReleaseVersion(right)) {
    throw new UpdateCheckError('invalid_release_version');
  }
  const leftParts = left.split('.').map(Number);
  const rightParts = right.split('.').map(Number);
  for (let index = 0; index < leftParts.length; index += 1) {
    if (leftParts[index] !== rightParts[index]) return leftParts[index] < rightParts[index] ? -1 : 1;
  }
  return 0;
}

function toDesktopSemver(version) {
  if (!isValidReleaseVersion(version)) throw new UpdateCheckError('invalid_release_version');
  const [year, month, day, build] = version.split('.').map(Number);
  return `${year}.${month}.${day}-${build}`;
}

function desktopTarget(platform, arch) {
  const platformName = { win32: 'windows', darwin: 'macos', linux: 'linux' }[platform];
  if (!platformName || !['x64', 'arm64'].includes(arch)) {
    throw new UpdateCheckError('unsupported_desktop_target');
  }
  return `${platformName}-${arch}`;
}

function assetNamesFor(version, channel, platform, arch) {
  const prefix = `V8-Agent-OS-${channel === 'preview' ? 'preview-' : ''}${version}`;
  const suffixes = {
    win32: [`win-${arch}-setup.exe`],
    darwin: [`macos-${arch}.dmg`],
    linux: [`linux-${arch}.AppImage`, `linux-${arch}.deb`],
  }[platform];
  if (!suffixes || !['x64', 'arm64'].includes(arch)) {
    throw new UpdateCheckError('unsupported_desktop_target');
  }
  return suffixes.map((suffix) => `${prefix}-${suffix}`);
}

function validateReleaseIdentity(manifest, appVersion, platform, arch) {
  if (!manifest || manifest.schema !== 2 || !manifest.release || !manifest.products?.desktop) {
    throw new UpdateCheckError('invalid_release_manifest');
  }
  const { version, channel, tag } = manifest.release;
  if (!isValidReleaseVersion(version) || !['preview', 'stable'].includes(channel)) {
    throw new UpdateCheckError('invalid_release_identity');
  }
  if (tag !== `v8-os-v${version}` || appVersion !== toDesktopSemver(version)) {
    throw new UpdateCheckError('release_identity_mismatch');
  }
  const target = desktopTarget(platform, arch);
  if (manifest.products.desktop.enabled !== true || manifest.products.desktop.targets?.[target]?.enabled !== true) {
    throw new UpdateCheckError('desktop_target_disabled');
  }
  return { version, channel, tag, target, platform, arch };
}

function loadReleaseIdentity(manifestPath, appVersion, platform, arch) {
  let manifest;
  try {
    manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  } catch {
    throw new UpdateCheckError('release_manifest_unavailable');
  }
  return validateReleaseIdentity(manifest, appVersion, platform, arch);
}

function releaseUrlForTag(tag) {
  if (!UNIFIED_TAG_RE.test(String(tag || ''))) throw new UpdateCheckError('invalid_release_tag');
  return `${RELEASE_WEB_BASE_URL}${encodeURIComponent(tag)}`;
}

function checksumUrlForTag(tag) {
  if (!UNIFIED_TAG_RE.test(String(tag || ''))) throw new UpdateCheckError('invalid_release_tag');
  return `${RELEASE_DOWNLOAD_BASE_URL}${encodeURIComponent(tag)}/SHA256SUMS.txt`;
}

function responseContentLength(response) {
  const raw = response?.headers?.get?.('content-length');
  if (raw === null || raw === undefined || raw === '') return null;
  if (!/^\d+$/.test(String(raw))) throw new UpdateCheckError('invalid_response_length');
  const length = Number(raw);
  if (!Number.isSafeInteger(length) || length < 0) throw new UpdateCheckError('invalid_response_length');
  return length;
}

async function cancelReader(reader) {
  try {
    await reader.cancel();
  } catch {}
}

async function readBoundedResponseText(response, maxBytes, tooLargeCode) {
  const declaredLength = responseContentLength(response);
  let reader = null;
  try {
    reader = response?.body?.getReader?.() || null;
  } catch {
    throw new UpdateCheckError('response_body_unavailable');
  }
  if (reader) {
    if (declaredLength !== null && declaredLength > maxBytes) {
      await cancelReader(reader);
      reader.releaseLock?.();
      throw new UpdateCheckError(tooLargeCode);
    }
    const chunks = [];
    let totalBytes = 0;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        if (!(value instanceof Uint8Array)) throw new UpdateCheckError('invalid_response_body');
        totalBytes += value.byteLength;
        if (totalBytes > maxBytes) {
          await cancelReader(reader);
          throw new UpdateCheckError(tooLargeCode);
        }
        chunks.push(Buffer.from(value.buffer, value.byteOffset, value.byteLength));
      }
    } finally {
      reader.releaseLock?.();
    }
    return Buffer.concat(chunks, totalBytes).toString('utf8');
  }

  // Test doubles without a ReadableStream are accepted only with an exact,
  // bounded Content-Length so production responses cannot silently bypass streaming limits.
  if (declaredLength === null || declaredLength > maxBytes || typeof response?.text !== 'function') {
    throw new UpdateCheckError(declaredLength !== null && declaredLength > maxBytes
      ? tooLargeCode
      : 'response_body_unavailable');
  }
  const text = await response.text();
  const actualLength = Buffer.byteLength(text, 'utf8');
  if (actualLength > maxBytes) throw new UpdateCheckError(tooLargeCode);
  if (actualLength !== declaredLength) throw new UpdateCheckError('response_length_mismatch');
  return text;
}

function validateChecksumResponseUrl(responseUrl, tag) {
  let actual;
  let expected;
  try {
    actual = new URL(String(responseUrl || ''));
    expected = new URL(checksumUrlForTag(tag));
  } catch {
    throw new UpdateCheckError('checksum_url_not_allowed');
  }
  if (actual.protocol !== 'https:') throw new UpdateCheckError('checksum_url_not_allowed');
  if (actual.href === expected.href) return;
  if (!CHECKSUM_REDIRECT_HOSTS.has(actual.hostname)) {
    throw new UpdateCheckError('checksum_url_not_allowed');
  }
}

function validateAssetChecksums(text, assetNames) {
  const expectedNames = new Set(assetNames);
  if (expectedNames.size === 0) throw new UpdateCheckError('checksum_entry_missing');
  const checksums = new Map();
  for (const rawLine of String(text || '').split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) continue;
    const match = /^(\S+)[ \t]+\*?(.+?)$/.exec(line);
    if (!match) continue;
    const digest = match[1];
    const assetName = match[2].trim();
    if (!expectedNames.has(assetName)) continue;
    if (!SHA256_RE.test(digest)) throw new UpdateCheckError('invalid_checksum_entry');
    const normalizedDigest = digest.toLowerCase();
    if (checksums.has(assetName) && checksums.get(assetName) !== normalizedDigest) {
      throw new UpdateCheckError('invalid_checksum_entry');
    }
    checksums.set(assetName, normalizedDigest);
  }
  for (const assetName of expectedNames) {
    if (!checksums.has(assetName)) throw new UpdateCheckError('checksum_entry_missing');
  }
}

function selectDesktopUpdate(releases, identity) {
  if (!Array.isArray(releases)) throw new UpdateCheckError('invalid_release_response');
  const expectedPrerelease = identity.channel === 'preview';
  const candidates = [];
  for (const release of releases) {
    const tagMatch = UNIFIED_TAG_RE.exec(String(release?.tag_name || ''));
    if (
      !tagMatch
      || release?.draft !== false
      || release?.prerelease !== expectedPrerelease
      || !Array.isArray(release?.assets)
    ) continue;
    const version = tagMatch[1];
    if (!isValidReleaseVersion(version) || compareReleaseVersions(version, identity.version) <= 0) continue;
    const uploadedAssets = new Set(
      release.assets
        .filter((asset) => asset?.state === 'uploaded' && Number(asset?.size) > 0)
        .map((asset) => String(asset.name || '')),
    );
    if (!uploadedAssets.has('SHA256SUMS.txt')) continue;
    const compatibleAssets = assetNamesFor(version, identity.channel, identity.platform, identity.arch);
    if (!compatibleAssets.every((name) => uploadedAssets.has(name))) continue;
    const tag = `v8-os-v${version}`;
    candidates.push({
      state: 'available',
      version,
      tag,
      releaseUrl: releaseUrlForTag(tag),
      assetNames: compatibleAssets,
      publishedAt: typeof release.published_at === 'string' ? release.published_at : null,
    });
  }
  candidates.sort((left, right) => compareReleaseVersions(right.version, left.version));
  return candidates[0] || null;
}

async function checkForDesktopUpdate(options) {
  const {
    fetchImpl,
    identity,
    timeoutMs = DEFAULT_UPDATE_TIMEOUT_MS,
  } = options || {};
  if (typeof fetchImpl !== 'function' || !identity) throw new UpdateCheckError('invalid_update_check_options');
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(RELEASES_API_URL, {
      method: 'GET',
      credentials: 'omit',
      redirect: 'error',
      signal: controller.signal,
      headers: {
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'V8-Agent-OS-Desktop',
      },
    });
    if (!response?.ok) {
      const status = Number(response?.status) || 0;
      throw new UpdateCheckError(status === 403 || status === 429 ? 'rate_limited' : `http_${status || 'error'}`);
    }
    const text = await readBoundedResponseText(response, MAX_RELEASE_RESPONSE_BYTES, 'release_response_too_large');
    let releases;
    try {
      releases = JSON.parse(text);
    } catch {
      throw new UpdateCheckError('invalid_release_response');
    }
    const update = selectDesktopUpdate(releases, identity);
    if (!update) return {
      state: 'current',
      version: identity.version,
    };
    const checksumUrl = checksumUrlForTag(update.tag);
    const checksumResponse = await fetchImpl(checksumUrl, {
      method: 'GET',
      credentials: 'omit',
      redirect: 'follow',
      signal: controller.signal,
      headers: {
        Accept: 'text/plain',
        'User-Agent': 'V8-Agent-OS-Desktop',
      },
    });
    if (!checksumResponse?.ok) {
      const status = Number(checksumResponse?.status) || 0;
      throw new UpdateCheckError(status === 403 || status === 429 ? 'rate_limited' : `checksum_http_${status || 'error'}`);
    }
    validateChecksumResponseUrl(checksumResponse.url, update.tag);
    const checksumText = await readBoundedResponseText(
      checksumResponse,
      MAX_CHECKSUM_RESPONSE_BYTES,
      'checksum_response_too_large',
    );
    validateAssetChecksums(checksumText, update.assetNames);
    return update;
  } catch (error) {
    if (error instanceof UpdateCheckError) throw error;
    if (controller.signal.aborted) throw new UpdateCheckError('timeout');
    throw new UpdateCheckError('network_unavailable');
  } finally {
    clearTimeout(timeout);
  }
}

module.exports = {
  DEFAULT_UPDATE_TIMEOUT_MS,
  RELEASES_API_URL,
  UpdateCheckError,
  assetNamesFor,
  checkForDesktopUpdate,
  checksumUrlForTag,
  compareReleaseVersions,
  loadReleaseIdentity,
  releaseUrlForTag,
  selectDesktopUpdate,
  toDesktopSemver,
  validateReleaseIdentity,
};
