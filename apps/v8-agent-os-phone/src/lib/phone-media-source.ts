import { useCallback, useEffect, useMemo, useState } from "react";

import { saveResponseToCache } from "@/src/lib/file-transfer";
import { fetchWorkspaceFileResponse } from "@/src/lib/phone-api";
import {
    isPhonePreviewBlockedByLoopback,
    resolveRenderableMediaCandidates,
    resolveRenderableMediaUrl,
    resolveWorkspaceSubpathFromMediaCandidate,
} from "@/src/lib/workspace-links";
import { useAppSession } from "@/src/providers/app-session";

const materializedMediaCache = new Map<string, Promise<string>>();

function extractCandidateFilename(candidate: string, fallbackTitle?: string) {
    const normalizedCandidate = String(candidate || "").trim();
    if (normalizedCandidate) {
        const withoutQuery = normalizedCandidate.split("?")[0] || normalizedCandidate;
        const fileName = withoutQuery.split(/[\\/]/).filter(Boolean).pop();
        if (fileName) {
            return fileName;
        }
    }
    const normalizedTitle = String(fallbackTitle || "").trim();
    if (normalizedTitle) {
        return normalizedTitle;
    }
    return `media-${Date.now()}`;
}

async function materializeWorkspaceMediaUri(
    authorizedFetch: ReturnType<typeof useAppSession>["authorizedFetch"],
    candidate: string,
    title?: string,
) {
    const workspaceSubpath = resolveWorkspaceSubpathFromMediaCandidate(candidate);
    if (!workspaceSubpath) {
        return candidate;
    }

    const cacheKey = `workspace:${workspaceSubpath}`;
    let pending = materializedMediaCache.get(cacheKey);
    if (!pending) {
        pending = (async () => {
            const response = await fetchWorkspaceFileResponse(authorizedFetch, workspaceSubpath);
            const saved = await saveResponseToCache(response, {
                prefix: "media",
                filename: extractCandidateFilename(workspaceSubpath, title),
            });
            return saved.uri;
        })().catch((error) => {
            materializedMediaCache.delete(cacheKey);
            throw error;
        });
        materializedMediaCache.set(cacheKey, pending);
    }
    return pending;
}

export function usePreparedPhoneMediaSource({
    src,
    candidates,
    title,
}: {
    src: string;
    candidates?: string[];
    title?: string;
}) {
    const { adminBaseUrl, authorizedFetch } = useAppSession();
    const candidateSources = useMemo(
        () => {
            const provided = Array.isArray(candidates) ? candidates.filter(Boolean) : [];
            if (provided.length > 0) {
                return Array.from(new Set(provided));
            }
            return resolveRenderableMediaCandidates(adminBaseUrl, src);
        },
        [adminBaseUrl, candidates, src],
    );
    const [candidateIndex, setCandidateIndex] = useState(0);
    const [resolvedSrc, setResolvedSrc] = useState("");
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const candidateSignature = useMemo(() => candidateSources.join("|"), [candidateSources]);

    const rawCandidate = useMemo(
        () => candidateSources[candidateIndex] || candidateSources[0] || resolveRenderableMediaUrl(adminBaseUrl, src) || src,
        [adminBaseUrl, candidateIndex, candidateSources, src],
    );
    const previewBlocked = isPhonePreviewBlockedByLoopback(adminBaseUrl, rawCandidate);

    useEffect(() => {
        setCandidateIndex(0);
    }, [candidateSignature]);

    const advanceCandidate = useCallback(() => {
        setCandidateIndex((value) => (value < candidateSources.length - 1 ? value + 1 : value));
    }, [candidateSources.length]);

    useEffect(() => {
        let cancelled = false;

        if (!rawCandidate) {
            setResolvedSrc("");
            setLoading(false);
            setError("");
            return () => {
                cancelled = true;
            };
        }

        const workspaceSubpath = resolveWorkspaceSubpathFromMediaCandidate(rawCandidate);
        if (!workspaceSubpath || previewBlocked) {
            setResolvedSrc(rawCandidate);
            setLoading(false);
            setError("");
            return () => {
                cancelled = true;
            };
        }

        setResolvedSrc("");
        setLoading(true);
        setError("");
        void materializeWorkspaceMediaUri(authorizedFetch, rawCandidate, title)
            .then((uri) => {
                if (cancelled) {
                    return;
                }
                setResolvedSrc(uri);
                setLoading(false);
            })
            .catch((reason) => {
                if (cancelled) {
                    return;
                }
                if (candidateIndex < candidateSources.length - 1) {
                    setCandidateIndex((value) => (value < candidateSources.length - 1 ? value + 1 : value));
                    return;
                }
                setResolvedSrc("");
                setLoading(false);
                setError(reason instanceof Error ? reason.message : String(reason || "媒体加载失败"));
            });

        return () => {
            cancelled = true;
        };
    }, [adminBaseUrl, authorizedFetch, candidateIndex, candidateSources.length, previewBlocked, rawCandidate, title]);

    return {
        candidateSources,
        candidateIndex,
        resolvedSrc,
        previewBlocked,
        loading,
        error,
        advanceCandidate,
    };
}
