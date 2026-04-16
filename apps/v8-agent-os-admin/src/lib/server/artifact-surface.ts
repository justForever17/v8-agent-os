import type { NextRequest } from "next/server";

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

function shouldRewriteToArtifactContent(value: string) {
    if (!value) {
        return true;
    }
    if (LOCAL_ARTIFACT_CONTENT_PATTERN.test(value)) {
        return true;
    }
    return !ABSOLUTE_HTTP_URL_PATTERN.test(value);
}

export function normalizeArtifactForAdminSurface(record: unknown, req: NextRequest) {
    if (!record || typeof record !== "object" || Array.isArray(record)) {
        return record;
    }

    const next = { ...(record as Record<string, unknown>) };
    const artifactId = artifactIdOf(next);
    if (!artifactId || !hasLocalBacking(next)) {
        return next;
    }

    const encodedId = encodeURIComponent(artifactId);
    const adminContentPath = `/api/memory/artifacts/${encodedId}/content`;
    const clientContentPath = `/api/client/artifacts/${encodedId}/content`;
    const clientOrigin = resolveClientOrigin(req);
    const signedClientContentUrl = clientOrigin
        ? buildSignedClientSurfaceUrl(clientContentPath, { publicBaseUrl: clientOrigin })
        : "";
    const surfaceContentUrl = signedClientContentUrl || adminContentPath;

    const previewUrl = stringValue(next.previewUrl) || stringValue(next.preview_url);
    const contentUrl = stringValue(next.contentUrl) || stringValue(next.content_url);
    if (shouldRewriteToArtifactContent(previewUrl)) {
        next.previewUrl = surfaceContentUrl;
        next.preview_url = surfaceContentUrl;
    }
    if (shouldRewriteToArtifactContent(contentUrl)) {
        next.contentUrl = surfaceContentUrl;
        next.content_url = surfaceContentUrl;
    }

    const existingResourceRef = next.resourceRef && typeof next.resourceRef === "object" && !Array.isArray(next.resourceRef)
        ? next.resourceRef as Record<string, unknown>
        : {};
    next.resourceRef = {
        ...existingResourceRef,
        kind: "artifact_content",
        artifactId,
        adminPath: clientContentPath,
        signedUrl: signedClientContentUrl || stringValue(existingResourceRef.signedUrl),
        previewable: true,
    };
    next.hasPreview = true;
    return next;
}

export function normalizeArtifactsForAdminSurface(records: unknown, req: NextRequest) {
    if (!Array.isArray(records)) {
        return [];
    }
    return records.map((record) => normalizeArtifactForAdminSurface(record, req));
}
