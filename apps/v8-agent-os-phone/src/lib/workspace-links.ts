import { resolveAdminAssetUrl } from "@/src/lib/admin-client";
import { getArtifactContentUrl, getWorkspaceFileUrl } from "@/src/lib/phone-api";

const LOOPBACK_WORKSPACE_URL_PATTERN = /https?:\/\/(?:127(?:\.\d{1,3}){3}|localhost|\[::1\])(?::\d+)?\/workspace\/([^\s)]+)/gi;
const LOOPBACK_WORKSPACE_API_URL_PATTERN = /https?:\/\/(?:127(?:\.\d{1,3}){3}|localhost|\[::1\])(?::\d+)?\/api\/workspace\/files\/([^\s)]+)/gi;
const LOOPBACK_ARTIFACT_CONTENT_URL_PATTERN = /https?:\/\/(?:127(?:\.\d{1,3}){3}|localhost|\[::1\])(?::\d+)?\/(?:v1|api(?:\/client)?)\/artifacts\/([^/\s)]+)\/content(?:\?[^\s)]*)?/gi;
const ABSOLUTE_WORKSPACE_PATH_PATTERN = /(^|[\s(])\/workspace\/([^\s)]+)/gi;
const ABSOLUTE_WORKSPACE_API_PATH_PATTERN = /(^|[\s(])\/api\/workspace\/files\/([^\s)]+)/gi;
const ABSOLUTE_ARTIFACT_CONTENT_PATH_PATTERN = /(^|[\s(])\/(?:v1|api(?:\/client)?)\/artifacts\/([^/\s)]+)\/content(?:\?[^\s)]*)?/gi;
const WINDOWS_WORKSPACE_PATH_PATTERN = /([A-Za-z]:\\[^\s<>"]*?\\workspace\\[^\s<>"]+)/g;

function safeDecodePath(value: string) {
    return String(value || "")
        .split("/")
        .map((segment) => {
            try {
                return decodeURIComponent(segment);
            } catch {
                return segment;
            }
        })
        .join("/");
}

function normalizeWorkspaceSubpath(value: string) {
    return safeDecodePath(String(value || ""))
        .replace(/\\/g, "/")
        .replace(/^\/+/, "")
        .replace(/^workspace\//i, "")
        .replace(/^api\/workspace\/files\//i, "")
        .replace(/^api\/client\/workspace\/files\//i, "")
        .trim();
}

export function deriveWorkspaceSubpathFromWindowsPath(rawValue: string) {
    const normalized = String(rawValue || "").trim();
    if (!normalized) {
        return null;
    }
    const marker = normalized.toLowerCase().lastIndexOf("\\workspace\\");
    if (marker < 0) {
        return null;
    }
    const subpath = normalizeWorkspaceSubpath(normalized.slice(marker + "\\workspace\\".length));
    return subpath || null;
}

export function normalizeRenderableWorkspaceUrl(adminBaseUrl: string, value?: string | null) {
    const raw = String(value || "").trim();
    if (!raw) {
        return "";
    }

    const loopbackMatch = raw.match(/^https?:\/\/(?:127(?:\.\d{1,3}){3}|localhost|\[::1\])(?::\d+)?\/workspace\/(.+)$/i);
    if (loopbackMatch?.[1]) {
        return getWorkspaceFileUrl(adminBaseUrl, normalizeWorkspaceSubpath(loopbackMatch[1]));
    }

    const loopbackApiMatch = raw.match(/^https?:\/\/(?:127(?:\.\d{1,3}){3}|localhost|\[::1\])(?::\d+)?\/api\/workspace\/files\/(.+)$/i);
    if (loopbackApiMatch?.[1]) {
        return getWorkspaceFileUrl(adminBaseUrl, normalizeWorkspaceSubpath(loopbackApiMatch[1]));
    }

    const loopbackArtifactMatch = raw.match(/^https?:\/\/(?:127(?:\.\d{1,3}){3}|localhost|\[::1\])(?::\d+)?\/(?:v1|api(?:\/client)?)\/artifacts\/([^/]+)\/content(?:\?.*)?$/i);
    if (loopbackArtifactMatch?.[1]) {
        return getArtifactContentUrl(adminBaseUrl, loopbackArtifactMatch[1]);
    }

    if (raw.startsWith("/workspace/")) {
        return getWorkspaceFileUrl(adminBaseUrl, normalizeWorkspaceSubpath(raw));
    }

    if (raw.startsWith("/api/workspace/files/")) {
        if (/[?&]v8(?:sig|exp)=/i.test(raw)) {
            return resolveAdminAssetUrl(adminBaseUrl, raw);
        }
        return getWorkspaceFileUrl(adminBaseUrl, normalizeWorkspaceSubpath(raw));
    }

    if (raw.startsWith("/api/client/workspace/files/") || raw.startsWith("api/client/workspace/files/")) {
        return resolveAdminAssetUrl(adminBaseUrl, raw.startsWith("/") ? raw : `/${raw}`);
    }

    if (
        (raw.startsWith("/api/client/artifacts/") || raw.startsWith("api/client/artifacts/"))
        && /[?&]v8(?:sig|exp)=/i.test(raw)
    ) {
        return resolveAdminAssetUrl(adminBaseUrl, raw.startsWith("/") ? raw : `/${raw}`);
    }

    const artifactContentMatch = raw.match(/^\/(?:v1|api(?:\/client)?)\/artifacts\/([^/]+)\/content(?:\?.*)?$/i);
    if (artifactContentMatch?.[1]) {
        return getArtifactContentUrl(adminBaseUrl, artifactContentMatch[1]);
    }

    const windowsSubpath = deriveWorkspaceSubpathFromWindowsPath(raw);
    if (windowsSubpath) {
        return getWorkspaceFileUrl(adminBaseUrl, windowsSubpath);
    }

    return resolveAdminAssetUrl(adminBaseUrl, raw);
}

export function normalizeRenderableWorkspaceLinks(adminBaseUrl: string, content: string) {
    let normalized = String(content || "");

    normalized = normalized.replace(LOOPBACK_WORKSPACE_URL_PATTERN, (_match, subpath: string) => {
        return getWorkspaceFileUrl(adminBaseUrl, normalizeWorkspaceSubpath(subpath));
    });

    normalized = normalized.replace(LOOPBACK_WORKSPACE_API_URL_PATTERN, (_match, subpath: string) => {
        return getWorkspaceFileUrl(adminBaseUrl, normalizeWorkspaceSubpath(subpath));
    });

    normalized = normalized.replace(LOOPBACK_ARTIFACT_CONTENT_URL_PATTERN, (_match, artifactId: string) => {
        return getArtifactContentUrl(adminBaseUrl, String(artifactId || "").trim());
    });

    normalized = normalized.replace(ABSOLUTE_WORKSPACE_PATH_PATTERN, (_match, prefix: string, subpath: string) => {
        return `${prefix}${getWorkspaceFileUrl(adminBaseUrl, normalizeWorkspaceSubpath(subpath))}`;
    });

    normalized = normalized.replace(ABSOLUTE_WORKSPACE_API_PATH_PATTERN, (_match, prefix: string, subpath: string) => {
        return `${prefix}${getWorkspaceFileUrl(adminBaseUrl, normalizeWorkspaceSubpath(subpath))}`;
    });

    normalized = normalized.replace(ABSOLUTE_ARTIFACT_CONTENT_PATH_PATTERN, (_match, prefix: string, artifactId: string) => {
        return `${prefix}${getArtifactContentUrl(adminBaseUrl, String(artifactId || "").trim())}`;
    });

    return normalized;
}

export function mapWindowsWorkspacePathToRenderableLink(adminBaseUrl: string, content: string) {
    const mapping = new Map<string, string>();
    for (const match of String(content || "").matchAll(WINDOWS_WORKSPACE_PATH_PATTERN)) {
        const rawPath = String(match[1] || "").trim();
        const subpath = deriveWorkspaceSubpathFromWindowsPath(rawPath);
        if (!rawPath || !subpath || mapping.has(rawPath)) {
            continue;
        }
        mapping.set(rawPath, getWorkspaceFileUrl(adminBaseUrl, subpath));
    }
    return mapping;
}
