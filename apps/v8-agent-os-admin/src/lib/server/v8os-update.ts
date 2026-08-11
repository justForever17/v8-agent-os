import fs from "node:fs";
import path from "node:path";

const RELEASES_API_URL = "https://api.github.com/repos/justForever17/v8-agent-os/releases?per_page=20";
const RELEASE_WEB_BASE_URL = "https://github.com/justForever17/v8-agent-os/releases/tag/";
const RELEASE_DOWNLOAD_BASE_URL = "https://github.com/justForever17/v8-agent-os/releases/download/";
const UPDATE_CACHE_TTL_MS = 5 * 60 * 1000;
const UPDATE_REQUEST_TIMEOUT_MS = 5_000;
const MAX_RELEASE_RESPONSE_BYTES = 1024 * 1024;
const MAX_CHECKSUM_RESPONSE_BYTES = 64 * 1024;
const VERSION_RE = /^20\d{2}\.(?:0[1-9]|1[0-2])\.(?:0[1-9]|[12]\d|3[01])\.(?:[1-9]|[1-9]\d)$/;
const UNIFIED_TAG_RE = /^v8-os-v(20\d{2}\.(?:0[1-9]|1[0-2])\.(?:0[1-9]|[12]\d|3[01])\.(?:[1-9]|[1-9]\d))$/;
const SHA256_RE = /^[a-f0-9]{64}$/i;
const CHECKSUM_REDIRECT_HOSTS = new Set(["release-assets.githubusercontent.com", "objects.githubusercontent.com"]);

type ReleaseIdentity = {
    version: string;
    channel: "preview" | "stable";
    tag: string;
    platformTarget: string;
    platform: NodeJS.Platform;
    arch: string;
};

type GitHubAsset = {
    name?: unknown;
    size?: unknown;
    state?: unknown;
};

type GitHubRelease = {
    tag_name?: unknown;
    draft?: unknown;
    prerelease?: unknown;
    html_url?: unknown;
    published_at?: unknown;
    assets?: GitHubAsset[];
};

export type V8OSUpdateState = {
    status: "available" | "current" | "incompatible" | "unavailable";
    currentVersion: string | null;
    currentTag: string | null;
    latestVersion: string | null;
    latestTag: string | null;
    compatibleVersion: string | null;
    releaseUrl: string | null;
    platformTarget: string | null;
    channel: "preview" | "stable" | null;
    checkedAt: string;
    action: "open_release_page";
    errorCode?: string;
};

type UpdateCacheEntry = {
    expiresAt: number;
    value: V8OSUpdateState;
};

let cachedUpdate: UpdateCacheEntry | null = null;
let updateRequest: Promise<V8OSUpdateState> | null = null;

function isValidReleaseVersion(version: string) {
    if (!VERSION_RE.test(version)) return false;
    const [year, month, day] = version.split(".").map(Number);
    const date = new Date(Date.UTC(year, month - 1, day));
    return date.getUTCFullYear() === year
        && date.getUTCMonth() === month - 1
        && date.getUTCDate() === day;
}

export function compareReleaseVersions(left: string, right: string) {
    if (!isValidReleaseVersion(left) || !isValidReleaseVersion(right)) {
        throw new Error("invalid_release_version");
    }
    const leftParts = left.split(".").map(Number);
    const rightParts = right.split(".").map(Number);
    for (let index = 0; index < leftParts.length; index += 1) {
        if (leftParts[index] !== rightParts[index]) {
            return leftParts[index] < rightParts[index] ? -1 : 1;
        }
    }
    return 0;
}

function platformTarget(platform: NodeJS.Platform, arch: string) {
    const platformNames: Partial<Record<NodeJS.Platform, string>> = {
        win32: "windows",
        darwin: "macos",
        linux: "linux",
    };
    const platformName = platformNames[platform];
    if (!platformName || !["x64", "arm64"].includes(arch)) {
        throw new Error("unsupported_desktop_target");
    }
    return `${platformName}-${arch}`;
}

function installerAssetNames(version: string, channel: "preview" | "stable", platform: NodeJS.Platform, arch: string) {
    const prefix = `V8-Agent-OS-${channel === "preview" ? "preview-" : ""}${version}`;
    const platformSuffixes: Partial<Record<NodeJS.Platform, string[]>> = {
        win32: [`win-${arch}-setup.exe`],
        darwin: [`macos-${arch}.dmg`],
        linux: [`linux-${arch}.AppImage`, `linux-${arch}.deb`],
    };
    const suffixes = platformSuffixes[platform];
    if (!suffixes || !["x64", "arm64"].includes(arch)) {
        throw new Error("unsupported_desktop_target");
    }
    return suffixes.map((suffix) => `${prefix}-${suffix}`);
}

function resolveReleaseManifestPath(explicitPath?: string) {
    const candidates = [
        explicitPath,
        process.env.V8_AGENT_OS_REPO_ROOT
            ? path.join(process.env.V8_AGENT_OS_REPO_ROOT, "release-manifest.json")
            : "",
        path.join(process.cwd(), "release-manifest.json"),
        path.resolve(process.cwd(), "..", "..", "release-manifest.json"),
    ].filter(Boolean) as string[];
    const match = candidates.find((candidate) => fs.existsSync(candidate));
    if (!match) throw new Error("release_manifest_unavailable");
    return match;
}

export function readReleaseIdentity(options: {
    manifestPath?: string;
    platform?: NodeJS.Platform;
    arch?: string;
} = {}): ReleaseIdentity {
    const platform = options.platform || process.platform;
    const arch = options.arch || process.arch;
    const target = platformTarget(platform, arch);
    const manifest = JSON.parse(fs.readFileSync(resolveReleaseManifestPath(options.manifestPath), "utf8"));
    const version = String(manifest?.release?.version || "");
    const channel = manifest?.release?.channel;
    const tag = String(manifest?.release?.tag || "");
    if (
        manifest?.schema !== 2
        || !isValidReleaseVersion(version)
        || !["preview", "stable"].includes(channel)
        || tag !== `v8-os-v${version}`
        || manifest?.products?.desktop?.enabled !== true
        || manifest?.products?.desktop?.targets?.[target]?.enabled !== true
    ) {
        throw new Error("invalid_release_manifest");
    }
    return { version, channel, tag, platformTarget: target, platform, arch };
}

function controlledReleaseUrl(tag: string) {
    if (!UNIFIED_TAG_RE.test(tag)) throw new Error("invalid_release_tag");
    return `${RELEASE_WEB_BASE_URL}${encodeURIComponent(tag)}`;
}

function checksumUrlForTag(tag: string) {
    if (!UNIFIED_TAG_RE.test(tag)) throw new Error("invalid_release_tag");
    return `${RELEASE_DOWNLOAD_BASE_URL}${encodeURIComponent(tag)}/SHA256SUMS.txt`;
}

function responseContentLength(response: Response) {
    const raw = response.headers.get("content-length");
    if (raw === null || raw === "") return null;
    if (!/^\d+$/.test(raw)) throw new Error("invalid_response_length");
    const value = Number(raw);
    if (!Number.isSafeInteger(value) || value < 0) throw new Error("invalid_response_length");
    return value;
}

async function readBoundedResponseText(response: Response, maxBytes: number, tooLargeCode: string) {
    const declaredLength = responseContentLength(response);
    const reader = response.body?.getReader();
    if (!reader) throw new Error("response_body_unavailable");
    if (declaredLength !== null && declaredLength > maxBytes) {
        await reader.cancel().catch(() => undefined);
        reader.releaseLock();
        throw new Error(tooLargeCode);
    }
    const chunks: Uint8Array[] = [];
    let totalBytes = 0;
    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            totalBytes += value.byteLength;
            if (totalBytes > maxBytes) {
                await reader.cancel().catch(() => undefined);
                throw new Error(tooLargeCode);
            }
            chunks.push(value);
        }
    } finally {
        reader.releaseLock();
    }
    const merged = new Uint8Array(totalBytes);
    let offset = 0;
    for (const chunk of chunks) {
        merged.set(chunk, offset);
        offset += chunk.byteLength;
    }
    return new TextDecoder().decode(merged);
}

function validateChecksumResponseUrl(responseUrl: string, tag: string) {
    let actual: URL;
    let expected: URL;
    try {
        actual = new URL(responseUrl);
        expected = new URL(checksumUrlForTag(tag));
    } catch {
        throw new Error("checksum_url_not_allowed");
    }
    if (actual.protocol !== "https:") throw new Error("checksum_url_not_allowed");
    if (actual.href === expected.href) return;
    if (!CHECKSUM_REDIRECT_HOSTS.has(actual.hostname)) throw new Error("checksum_url_not_allowed");
}

function validateAssetChecksums(text: string, assetNames: string[]) {
    const expectedNames = new Set(assetNames);
    const checksums = new Map<string, string>();
    for (const rawLine of text.split(/\r?\n/)) {
        const match = /^(\S+)[ \t]+\*?(.+?)$/.exec(rawLine.trim());
        if (!match || !expectedNames.has(match[2].trim())) continue;
        const digest = match[1];
        const assetName = match[2].trim();
        if (!SHA256_RE.test(digest)) throw new Error("invalid_checksum_entry");
        const normalized = digest.toLowerCase();
        if (checksums.has(assetName) && checksums.get(assetName) !== normalized) {
            throw new Error("invalid_checksum_entry");
        }
        checksums.set(assetName, normalized);
    }
    if ([...expectedNames].some((assetName) => !checksums.has(assetName))) {
        throw new Error("checksum_entry_missing");
    }
}

export function selectV8OSUpdate(releases: GitHubRelease[], identity: ReleaseIdentity, checkedAt: string): V8OSUpdateState {
    if (!Array.isArray(releases)) throw new Error("invalid_release_response");
    const expectedPrerelease = identity.channel === "preview";
    const candidates = releases.flatMap((release) => {
        const tagMatch = UNIFIED_TAG_RE.exec(String(release?.tag_name || ""));
        if (!tagMatch || release?.draft !== false || release?.prerelease !== expectedPrerelease) return [];
        const version = tagMatch[1];
        if (!isValidReleaseVersion(version)) return [];
        const tag = `v8-os-v${version}`;
        const expectedUrl = controlledReleaseUrl(tag);
        if (String(release?.html_url || "") !== expectedUrl) return [];
        const uploadedAssets = new Set(
            (Array.isArray(release?.assets) ? release.assets : [])
                .filter((asset) => asset?.state === "uploaded" && Number(asset?.size) > 0)
                .map((asset) => String(asset?.name || "")),
        );
        const requiredAssets = installerAssetNames(version, identity.channel, identity.platform, identity.arch);
        const compatible = uploadedAssets.has("SHA256SUMS.txt")
            && requiredAssets.every((assetName) => uploadedAssets.has(assetName));
        return [{ version, tag, releaseUrl: expectedUrl, compatible }];
    }).sort((left, right) => compareReleaseVersions(right.version, left.version));

    const latest = candidates[0] || null;
    const compatible = candidates.find((candidate) => candidate.compatible) || null;
    const hasUpgrade = compatible
        ? compareReleaseVersions(compatible.version, identity.version) > 0
        : false;
    const latestIsNewer = latest
        ? compareReleaseVersions(latest.version, identity.version) > 0
        : false;
    const status = hasUpgrade
        ? "available"
        : latestIsNewer
            ? "incompatible"
            : "current";

    const latestAtLeastCurrent = latest && compareReleaseVersions(latest.version, identity.version) >= 0
        ? latest
        : null;
    return {
        status,
        currentVersion: identity.version,
        currentTag: identity.tag,
        latestVersion: latestAtLeastCurrent?.version || identity.version,
        latestTag: latestAtLeastCurrent?.tag || identity.tag,
        compatibleVersion: compatible?.version || null,
        releaseUrl: status === "available"
            ? compatible?.releaseUrl || null
            : latestAtLeastCurrent?.releaseUrl || controlledReleaseUrl(identity.tag),
        platformTarget: identity.platformTarget,
        channel: identity.channel,
        checkedAt,
        action: "open_release_page",
    };
}

async function fetchReleaseState(identity: ReleaseIdentity, fetchImpl: typeof fetch, now: number) {
    const checkedAt = new Date(now).toISOString();
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), UPDATE_REQUEST_TIMEOUT_MS);
    try {
        const response = await fetchImpl(RELEASES_API_URL, {
            method: "GET",
            cache: "no-store",
            credentials: "omit",
            redirect: "error",
            signal: controller.signal,
            headers: {
                Accept: "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "V8-Agent-OS-Admin",
            },
        });
        if (!response.ok) {
            throw new Error(response.status === 403 || response.status === 429 ? "rate_limited" : "release_service_unavailable");
        }
        const text = await readBoundedResponseText(response, MAX_RELEASE_RESPONSE_BYTES, "release_response_too_large");
        const selected = selectV8OSUpdate(JSON.parse(text) as GitHubRelease[], identity, checkedAt);
        if (selected.status !== "available" || !selected.compatibleVersion) return selected;

        const tag = `v8-os-v${selected.compatibleVersion}`;
        const checksumResponse = await fetchImpl(checksumUrlForTag(tag), {
            method: "GET",
            cache: "no-store",
            credentials: "omit",
            redirect: "follow",
            signal: controller.signal,
            headers: {
                Accept: "text/plain",
                "User-Agent": "V8-Agent-OS-Admin",
            },
        });
        if (!checksumResponse.ok) {
            throw new Error(checksumResponse.status === 403 || checksumResponse.status === 429
                ? "rate_limited"
                : "release_service_unavailable");
        }
        validateChecksumResponseUrl(checksumResponse.url, tag);
        const checksumText = await readBoundedResponseText(
            checksumResponse,
            MAX_CHECKSUM_RESPONSE_BYTES,
            "checksum_response_too_large",
        );
        validateAssetChecksums(
            checksumText,
            installerAssetNames(selected.compatibleVersion, identity.channel, identity.platform, identity.arch),
        );
        return selected;
    } catch (error) {
        const candidateCode = controller.signal.aborted
            ? "timeout"
            : error instanceof SyntaxError
                ? "invalid_release_response"
                : String(error instanceof Error ? error.message : "release_service_unavailable");
        const errorCode = [
            "timeout",
            "invalid_release_response",
            "rate_limited",
            "release_service_unavailable",
            "release_response_too_large",
            "checksum_response_too_large",
            "checksum_url_not_allowed",
            "checksum_entry_missing",
            "invalid_checksum_entry",
            "invalid_response_length",
            "response_body_unavailable",
        ].includes(candidateCode)
            ? candidateCode
            : "network_unavailable";
        return {
            status: "unavailable" as const,
            currentVersion: identity.version,
            currentTag: identity.tag,
            latestVersion: null,
            latestTag: null,
            compatibleVersion: null,
            releaseUrl: controlledReleaseUrl(identity.tag),
            platformTarget: identity.platformTarget,
            channel: identity.channel,
            checkedAt,
            action: "open_release_page" as const,
            errorCode,
        };
    } finally {
        clearTimeout(timeoutId);
    }
}

export async function getV8OSUpdateState(options: {
    force?: boolean;
    manifestPath?: string;
    platform?: NodeJS.Platform;
    arch?: string;
    fetchImpl?: typeof fetch;
    now?: number;
} = {}) {
    const now = options.now ?? Date.now();
    if (!options.force && cachedUpdate && cachedUpdate.expiresAt > now) {
        return cachedUpdate.value;
    }
    if (updateRequest) return updateRequest;

    let identity: ReleaseIdentity;
    try {
        identity = readReleaseIdentity(options);
    } catch (error) {
        return {
            status: "unavailable" as const,
            currentVersion: null,
            currentTag: null,
            latestVersion: null,
            latestTag: null,
            compatibleVersion: null,
            releaseUrl: null,
            platformTarget: null,
            channel: null,
            checkedAt: new Date(now).toISOString(),
            action: "open_release_page" as const,
            errorCode: String(error instanceof Error ? error.message : "release_manifest_unavailable"),
        };
    }

    const request = fetchReleaseState(identity, options.fetchImpl || fetch, now);
    updateRequest = request;
    try {
        const value = await request;
        cachedUpdate = { value, expiresAt: now + UPDATE_CACHE_TTL_MS };
        return value;
    } finally {
        if (updateRequest === request) updateRequest = null;
    }
}

export function clearV8OSUpdateCache() {
    cachedUpdate = null;
    updateRequest = null;
}

export const V8OS_UPDATE_CONTRACT = Object.freeze({
    releasesApiUrl: RELEASES_API_URL,
    cacheTtlMs: UPDATE_CACHE_TTL_MS,
    requestTimeoutMs: UPDATE_REQUEST_TIMEOUT_MS,
});
