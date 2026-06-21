import { access, copyFile, mkdir } from "fs/promises";
import os from "os";
import path from "path";
import { PLATFORM_LOGIN_PRESETS, type PlatformLoginPreset } from "@/lib/models/provider-admin";

const INVISIBLE_OAUTH_PATH_MARKERS = /[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]/g;

function sanitizeOauthPath(filepath: string): string {
    return String(filepath || "").replace(INVISIBLE_OAUTH_PATH_MARKERS, "").trim();
}

function expandHome(filepath: string): string {
    if (filepath === "~") return os.homedir();
    if (filepath.startsWith("~/") || filepath.startsWith("~\\")) {
        return path.join(os.homedir(), filepath.slice(2));
    }
    return filepath;
}

function slugifyProviderId(providerId: string): string {
    const slug = String(providerId || "provider")
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9_-]+/g, "-")
        .replace(/^-+|-+$/g, "");
    return slug || "provider";
}

function canonicalOauthRoot(): string {
    return path.join(os.homedir(), ".v8-agent-os", "core", "oauth", "providers");
}

function shouldPreserveLiveOauthSource(platformLoginPreset?: string): boolean {
    return platformLoginPreset === "codex";
}

function isCanonicalOauthRuntimePath(filepath: string): boolean {
    const resolved = path.resolve(expandHome(filepath));
    const canonicalRoot = path.resolve(canonicalOauthRoot());
    return resolved === canonicalRoot || resolved.startsWith(`${canonicalRoot}${path.sep}`);
}

export async function canonicalizeOauthCredentialReference(params: {
    providerId: string;
    rawReference: string;
    platformLoginPreset?: PlatformLoginPreset;
}): Promise<{
    storedCredential: string;
    canonicalPath: string;
    sourcePath: string;
    oauthRef: string;
}> {
    const normalized = sanitizeOauthPath(String(params.rawReference || ""));
    if (!normalized) {
        return {
            storedCredential: "",
            canonicalPath: "",
            sourcePath: "",
            oauthRef: "",
        };
    }

    const sourceValue = normalized.startsWith("oauth:") ? normalized.slice(6) : normalized;
    const preserveLiveSource = shouldPreserveLiveOauthSource(params.platformLoginPreset);
    const preferredLiveSource = preserveLiveSource && params.platformLoginPreset
        ? PLATFORM_LOGIN_PRESETS[params.platformLoginPreset].oauthPath
        : "";
    const effectiveSourceValue = preserveLiveSource && isCanonicalOauthRuntimePath(sourceValue) && preferredLiveSource
        ? preferredLiveSource
        : sourceValue;
    const sourcePath = path.resolve(expandHome(effectiveSourceValue));
    await access(sourcePath);

    if (preserveLiveSource) {
        return {
            storedCredential: `oauth:${sourcePath}`,
            canonicalPath: "",
            sourcePath,
            oauthRef: "",
        };
    }

    const providerDir = path.join(canonicalOauthRoot(), slugifyProviderId(params.providerId));
    await mkdir(providerDir, { recursive: true });

    const sourceExt = path.extname(sourcePath) || ".json";
    const canonicalFileName = params.platformLoginPreset
        ? `${params.platformLoginPreset}${sourceExt}`
        : path.basename(sourcePath) || `credential${sourceExt}`;
    const canonicalPath = path.join(providerDir, canonicalFileName);

    if (path.normalize(sourcePath) !== path.normalize(canonicalPath)) {
        await copyFile(sourcePath, canonicalPath);
    }

    return {
        storedCredential: `oauth:${canonicalPath}`,
        canonicalPath,
        sourcePath,
        oauthRef: path.relative(canonicalOauthRoot(), canonicalPath).replace(/\\/g, "/"),
    };
}
