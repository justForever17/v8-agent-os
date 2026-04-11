import { resolveAdminAssetUrl } from "@/src/lib/admin-client";
import { getWorkspaceFileUrl } from "@/src/lib/phone-api";
import { resolveAdminResourceUrl, type AdminResourceRef } from "@v8/session-realtime";

const LOOPBACK_URL_PATTERN = /^https?:\/\/(?:127(?:\.\d{1,3}){3}|localhost|\[::1\])(?::\d+)?(?:\/|$)/i;
const LOOPBACK_WORKSPACE_URL_PATTERN = /^https?:\/\/(?:127(?:\.\d{1,3}){3}|localhost|\[::1\])(?::\d+)?\/workspace\/(.+)$/i;
const LOOPBACK_WORKSPACE_API_URL_PATTERN = /^https?:\/\/(?:127(?:\.\d{1,3}){3}|localhost|\[::1\])(?::\d+)?\/api\/workspace\/files\/(.+)$/i;
const ABSOLUTE_ADMIN_WORKSPACE_API_URL_PATTERN = /^https?:\/\/.+\/api\/client\/workspace\/files\/(.+)$/i;
const RELATIVE_WORKSPACE_PATH_PATTERN = /^\/?workspace\/(.+)$/i;
const RELATIVE_WORKSPACE_API_PATH_PATTERN = /^\/?api\/workspace\/files\/(.+)$/i;
const RELATIVE_ADMIN_WORKSPACE_PATH_PATTERN = /^\/?api\/client\/workspace\/files\/(.+)$/i;
const RELATIVE_DOWNLOADED_MEDIA_PATH_PATTERN = /^\/?(downloaded_media\/.+)$/i;
const RELATIVE_ARTIFACT_CONTENT_PATH_PATTERN = /^\/?(?:v1|api(?:\/client)?)\/artifacts\/[^/]+\/content(?:[?#].*)?$/i;
const ABSOLUTE_ARTIFACT_CONTENT_URL_PATTERN = /^https?:\/\/.+\/(?:v1|api(?:\/client)?)\/artifacts\/[^/]+\/content(?:[?#].*)?$/i;

function ensureLeadingSlash(value: string) {
    const normalized = String(value || "").trim();
    if (!normalized) {
        return "";
    }
    return normalized.startsWith("/") ? normalized : `/${normalized}`;
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

function resolveWorkspaceCandidateSubpath(value: unknown) {
    const raw = String(value || "").trim();
    if (!raw) {
        return "";
    }
    const windowsSubpath = deriveWorkspaceSubpathFromWindowsPath(raw);
    if (windowsSubpath) {
        return windowsSubpath;
    }
    if (/^[a-z]+:\/\//i.test(raw) || /^[a-z]:\\/i.test(raw)) {
        return "";
    }
    return normalizeWorkspaceSubpath(raw);
}

export function resolveWorkspaceSubpathFromMediaCandidate(value?: string | null) {
    const raw = String(value || "").trim();
    if (!raw) {
        return "";
    }

    const absoluteAdminWorkspaceApiMatch = raw.match(ABSOLUTE_ADMIN_WORKSPACE_API_URL_PATTERN);
    if (absoluteAdminWorkspaceApiMatch?.[1]) {
        return normalizeWorkspaceSubpath(absoluteAdminWorkspaceApiMatch[1]);
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

    const workspaceSubpath = resolveWorkspaceSubpathFromMediaCandidate(raw);
    if (workspaceSubpath) {
        return getWorkspaceFileUrl(adminBaseUrl, workspaceSubpath);
    }

    return resolveAdminAssetUrl(adminBaseUrl, raw);
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

    const resourceUrl = resolveAdminResourceUrl("phone", adminBaseUrl, normalizedOptions.resourceRef || null);
    pushUniqueCandidate(candidates, normalizeRenderableWorkspaceUrl(adminBaseUrl, resourceUrl));

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
        if (isArtifactContentPath(rawCandidate)) {
            continue;
        }
        const normalizedCandidate = normalizeRenderableWorkspaceUrl(adminBaseUrl, rawCandidate);
        if (!normalizedCandidate) {
            continue;
        }
        if (isArtifactContentUrl(normalizedCandidate) && !/[?&](?:v8sig|sig|token)=/i.test(normalizedCandidate)) {
            continue;
        }
        pushUniqueCandidate(candidates, normalizedCandidate);
    }

    const workspaceCandidates = [
        normalizedOptions.workspacePath,
        normalizedOptions.sourcePath,
    ];
    for (const candidate of workspaceCandidates) {
        const subpath = resolveWorkspaceCandidateSubpath(candidate);
        if (subpath) {
            pushUniqueCandidate(candidates, getWorkspaceFileUrl(adminBaseUrl, subpath));
        }
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
    adminBaseUrl: string,
    value?: string | null,
) {
    const normalizedUrl = normalizeRenderableWorkspaceUrl(adminBaseUrl, value);
    return Boolean(normalizedUrl) && isLoopbackUrl(normalizedUrl) && isLoopbackUrl(adminBaseUrl);
}
