import { resolveAdminAssetUrl } from "@/src/lib/admin-client";
import type { AdminResourceRef } from "@v8/session-realtime";

const ABSOLUTE_URL_PATTERN = /^[a-z][a-z0-9+.-]*:\/\//i;
const LOOPBACK_URL_PATTERN = /^https?:\/\/(?:127(?:\.\d{1,3}){3}|localhost|\[::1\])(?::\d+)?(?:\/|$)/i;
const LOOPBACK_WORKSPACE_URL_PATTERN = /^https?:\/\/(?:127(?:\.\d{1,3}){3}|localhost|\[::1\])(?::\d+)?\/workspace\/(.+)$/i;
const LOOPBACK_WORKSPACE_API_URL_PATTERN = /^https?:\/\/(?:127(?:\.\d{1,3}){3}|localhost|\[::1\])(?::\d+)?\/api\/workspace\/files\/(.+)$/i;
const RELATIVE_WORKSPACE_PATH_PATTERN = /^\/?workspace\/(.+)$/i;
const RELATIVE_WORKSPACE_API_PATH_PATTERN = /^\/?api\/workspace\/files\/(.+)$/i;
const RELATIVE_ADMIN_WORKSPACE_PATH_PATTERN = /^\/?api\/client\/workspace\/files\/(.+)$/i;
const RELATIVE_DOWNLOADED_MEDIA_PATH_PATTERN = /^\/?(downloaded_media\/.+)$/i;
const RELATIVE_ARTIFACT_CONTENT_PATH_PATTERN = /^\/?(?:v1|api(?:\/client)?)\/artifacts\/[^/]+\/content(?:[?#].*)?$/i;
const ABSOLUTE_ARTIFACT_CONTENT_URL_PATTERN = /^https?:\/\/.+\/(?:v1|api(?:\/client)?)\/artifacts\/[^/]+\/content(?:[?#].*)?$/i;
const RELATIVE_ADMIN_MEDIA_PATH_PATTERN = /^\/api\/client\/(?:workspace\/files\/.+|artifacts\/[^/]+\/content(?:[?#].*)?)$/i;
const RELATIVE_API_MEDIA_PATH_PATTERN = /^\/api\/(?:workspace\/files\/.+|artifacts\/[^/]+\/content(?:[?#].*)?)$/i;
const RELATIVE_WORKSPACE_MEDIA_PATH_PATTERN = /^\/workspace\/.+$/i;

function hasSignedSurfaceQuery(value: string) {
    return /[?&]v8(?:sig|exp)=/i.test(String(value || "").trim());
}

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

export function resolveWorkspaceSubpathFromMediaCandidate(value?: string | null) {
    const raw = String(value || "").trim();
    if (!raw) {
        return "";
    }

    const loopbackWorkspaceMatch = raw.match(LOOPBACK_WORKSPACE_URL_PATTERN);
    if (loopbackWorkspaceMatch?.[1]) {
        return normalizeWorkspaceSubpath(loopbackWorkspaceMatch[1]);
    }

    const loopbackWorkspaceApiMatch = raw.match(LOOPBACK_WORKSPACE_API_URL_PATTERN);
    if (loopbackWorkspaceApiMatch?.[1]) {
        return normalizeWorkspaceSubpath(loopbackWorkspaceApiMatch[1]);
    }

    const relativeWorkspaceMatch = raw.match(RELATIVE_WORKSPACE_PATH_PATTERN);
    if (relativeWorkspaceMatch?.[1]) {
        return normalizeWorkspaceSubpath(relativeWorkspaceMatch[1]);
    }

    const relativeWorkspaceApiMatch = raw.match(RELATIVE_WORKSPACE_API_PATH_PATTERN);
    if (relativeWorkspaceApiMatch?.[1]) {
        return normalizeWorkspaceSubpath(relativeWorkspaceApiMatch[1]);
    }

    const relativeAdminWorkspaceMatch = raw.match(RELATIVE_ADMIN_WORKSPACE_PATH_PATTERN);
    if (relativeAdminWorkspaceMatch?.[1]) {
        return normalizeWorkspaceSubpath(relativeAdminWorkspaceMatch[1]);
    }

    const relativeDownloadedMediaMatch = raw.match(RELATIVE_DOWNLOADED_MEDIA_PATH_PATTERN);
    if (relativeDownloadedMediaMatch?.[1]) {
        return normalizeWorkspaceSubpath(relativeDownloadedMediaMatch[1]);
    }

    const windowsSubpath = deriveWorkspaceSubpathFromWindowsPath(raw);
    if (windowsSubpath) {
        return windowsSubpath;
    }

    return "";
}

function isArtifactContentPath(value: string) {
    return RELATIVE_ARTIFACT_CONTENT_PATH_PATTERN.test(String(value || "").trim());
}

function isArtifactContentUrl(value: string) {
    return ABSOLUTE_ARTIFACT_CONTENT_URL_PATTERN.test(String(value || "").trim());
}

export function normalizeRenderableWorkspaceUrl(adminBaseUrl: string, value?: string | null) {
    const raw = String(value || "").trim();
    if (!raw) {
        return "";
    }

    if (ABSOLUTE_URL_PATTERN.test(raw)) {
        return raw;
    }

    const workspaceSubpath = resolveWorkspaceSubpathFromMediaCandidate(raw);
    if (workspaceSubpath) {
        const normalizedSubpath = workspaceSubpath
            .split("/")
            .filter(Boolean)
            .map((segment) => encodeURIComponent(segment))
            .join("/");
        return resolveAdminAssetUrl(adminBaseUrl, `/api/client/workspace/files/${normalizedSubpath}`);
    }

    return resolveAdminAssetUrl(adminBaseUrl, raw);
}

function normalizeDirectMediaUrl(adminBaseUrl: string, value?: string | null) {
    const raw = String(value || "").trim();
    if (!raw) {
        return "";
    }

    if (ABSOLUTE_URL_PATTERN.test(raw)) {
        return raw;
    }

    if (hasSignedSurfaceQuery(raw) && (
        RELATIVE_ADMIN_MEDIA_PATH_PATTERN.test(raw)
        || RELATIVE_API_MEDIA_PATH_PATTERN.test(raw)
        || RELATIVE_ARTIFACT_CONTENT_PATH_PATTERN.test(raw)
        || RELATIVE_WORKSPACE_MEDIA_PATH_PATTERN.test(raw)
    )) {
        return resolveAdminAssetUrl(adminBaseUrl, raw);
    }

    return "";
}

function resolveRenderableResourceUrl(resourceRef?: AdminResourceRef | null) {
    if (!resourceRef || resourceRef.previewable === false) {
        return "";
    }
    if (resourceRef.signedUrl) {
        return resourceRef.signedUrl;
    }
    if (resourceRef.kind === "external_url") {
        return String(resourceRef.url || "").trim();
    }
    return "";
}

function pushUniqueCandidate(target: string[], value?: string | null) {
    const normalized = String(value || "").trim();
    if (!normalized) {
        return;
    }
    if (!target.includes(normalized)) {
        target.push(normalized);
    }
}

export function resolveRenderableMediaCandidates(
    adminBaseUrl: string,
    options:
        | string
        | {
            value?: string | null;
            resourceRef?: AdminResourceRef | null;
            previewUrl?: string | null;
            externalUrl?: string | null;
            workspacePath?: string | null;
            sourcePath?: string | null;
        },
) {
    const normalizedOptions = typeof options === "string" ? { value: options } : (options || {});
    const candidates: string[] = [];

    const resourceRef = normalizedOptions.resourceRef || null;
    const resourceUrl = resolveRenderableResourceUrl(resourceRef);
    pushUniqueCandidate(candidates, normalizeDirectMediaUrl(adminBaseUrl, resourceUrl));

    const directCandidates = [
        normalizedOptions.previewUrl,
        normalizedOptions.externalUrl,
        normalizedOptions.value,
    ];
    for (const candidate of directCandidates) {
        const rawCandidate = String(candidate || "").trim();
        if (!rawCandidate) {
            continue;
        }
        if (isArtifactContentPath(rawCandidate) && !hasSignedSurfaceQuery(rawCandidate)) {
            continue;
        }
        const normalizedCandidate = normalizeDirectMediaUrl(adminBaseUrl, rawCandidate);
        if (!normalizedCandidate) {
            continue;
        }
        if (isArtifactContentUrl(normalizedCandidate) && !/[?&](?:v8sig|sig|token)=/i.test(normalizedCandidate)) {
            continue;
        }
        pushUniqueCandidate(candidates, normalizedCandidate);
    }

    const workspaceCandidates = [
        resourceRef?.workspacePath,
        normalizedOptions.workspacePath,
        normalizedOptions.sourcePath,
        normalizedOptions.value,
    ];
    for (const candidate of workspaceCandidates) {
        const normalizedCandidate = normalizeRenderableWorkspaceUrl(adminBaseUrl, candidate);
        if (!normalizedCandidate) {
            continue;
        }
        pushUniqueCandidate(candidates, normalizedCandidate);
    }

    return candidates;
}

export function resolveRenderableMediaUrl(
    adminBaseUrl: string,
    options:
        | string
        | {
            value?: string | null;
            resourceRef?: AdminResourceRef | null;
            previewUrl?: string | null;
            externalUrl?: string | null;
            workspacePath?: string | null;
            sourcePath?: string | null;
        },
) {
    return resolveRenderableMediaCandidates(adminBaseUrl, options)[0] || "";
}

export function isLoopbackUrl(value?: string | null) {
    const normalized = String(value || "").trim();
    if (!normalized) {
        return false;
    }
    return LOOPBACK_URL_PATTERN.test(normalized);
}

export function isPhonePreviewBlockedByLoopback(
    _adminBaseUrl: string,
    value?: string | null,
) {
    const normalizedUrl = String(value || "").trim();
    return Boolean(normalizedUrl) && isLoopbackUrl(normalizedUrl);
}
