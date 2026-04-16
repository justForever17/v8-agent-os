import type { NextRequest } from "next/server";
import {
    coerceAdminResourceRef,
    deriveAdminResourceRefFromArtifactLike,
    type AdminResourceRef,
} from "@v8/session-realtime";

import { buildSignedClientSurfaceUrl } from "@/lib/server/client-surface-resource";
import {
    resolveClientSurfaceOriginFromRequest,
} from "@/lib/server/runtime-config";

const ABSOLUTE_HTTP_URL_PATTERN = /^https?:\/\//i;
const LOCAL_ARTIFACT_CONTENT_PATTERN = /^\/?(?:v1|api(?:\/client|\/memory)?)\/artifacts\/[^/]+\/content(?:[?#].*)?$/i;

function stringValue(value: unknown) {
    return typeof value === "string" ? value.trim() : "";
}

function artifactIdOf(record: Record<string, unknown>) {
    return stringValue(record.artifactId) || stringValue(record.id);
}

function hasLocalBacking(record: Record<string, unknown>) {
    return Boolean(
        stringValue(record.sourcePath)
        || stringValue(record.source_path)
        || stringValue(record.workspacePath)
        || stringValue(record.workspace_path)
        || LOCAL_ARTIFACT_CONTENT_PATTERN.test(stringValue(record.previewUrl) || stringValue(record.preview_url))
        || LOCAL_ARTIFACT_CONTENT_PATTERN.test(stringValue(record.contentUrl) || stringValue(record.content_url)),
    );
}

function resolveClientOrigin(req: NextRequest) {
    return resolveClientSurfaceOriginFromRequest(req, { allowTrustedHeader: true });
}

function shouldRewriteToSurface(value: string) {
    if (!value) {
        return true;
    }
    if (LOCAL_ARTIFACT_CONTENT_PATTERN.test(value)) {
        return true;
    }
    return !ABSOLUTE_HTTP_URL_PATTERN.test(value);
}

function normalizeAdminSurfacePath(path: string) {
    const normalized = stringValue(path);
    if (!normalized) {
        return "";
    }
    if (normalized.startsWith("/api/client/")) {
        return normalized.replace(/^\/api\/client\b/i, "/api");
    }
    return normalized.startsWith("/") ? normalized : `/${normalized}`;
}

function attachSignedSurfaceUrl(resourceRef: AdminResourceRef | null, req: NextRequest) {
    if (!resourceRef || resourceRef.kind === "external_url") {
        return resourceRef;
    }
    const adminPath = stringValue(resourceRef.adminPath);
    if (!adminPath) {
        return resourceRef;
    }
    const clientOrigin = resolveClientOrigin(req);
    const signedUrl = clientOrigin
        ? buildSignedClientSurfaceUrl(adminPath, { publicBaseUrl: clientOrigin })
        : "";
    return {
        ...resourceRef,
        signedUrl: signedUrl || resourceRef.signedUrl,
    };
}

export function normalizeArtifactForAdminSurface(record: unknown, req: NextRequest) {
    if (!record || typeof record !== "object" || Array.isArray(record)) {
        return record;
    }

    const next = { ...(record as Record<string, unknown>) };
    const artifactId = artifactIdOf(next);
    const derivedResourceRef = attachSignedSurfaceUrl(
        coerceAdminResourceRef(next.resourceRef) || deriveAdminResourceRefFromArtifactLike(next),
        req,
    );
    if (!derivedResourceRef || (!artifactId && !hasLocalBacking(next))) {
        return next;
    }

    const encodedId = encodeURIComponent(artifactId);
    const adminContentPath = `/api/memory/artifacts/${encodedId}/content`;
    const adminSurfacePath = derivedResourceRef.kind === "artifact_content"
        ? adminContentPath
        : normalizeAdminSurfacePath(stringValue(derivedResourceRef.adminPath));
    const surfaceContentUrl = adminSurfacePath || adminContentPath;

    const previewUrl = stringValue(next.previewUrl) || stringValue(next.preview_url);
    const contentUrl = stringValue(next.contentUrl) || stringValue(next.content_url);
    if (shouldRewriteToSurface(previewUrl)) {
        next.previewUrl = surfaceContentUrl;
        next.preview_url = surfaceContentUrl;
    }
    if (shouldRewriteToSurface(contentUrl)) {
        next.contentUrl = surfaceContentUrl;
        next.content_url = surfaceContentUrl;
    }
    next.resourceRef = derivedResourceRef;
    next.hasPreview = Boolean(surfaceContentUrl) && derivedResourceRef.previewable !== false;
    return next;
}

export function normalizeArtifactsForAdminSurface(records: unknown, req: NextRequest) {
    if (!Array.isArray(records)) {
        return [];
    }
    return records.map((record) => normalizeArtifactForAdminSurface(record, req));
}
