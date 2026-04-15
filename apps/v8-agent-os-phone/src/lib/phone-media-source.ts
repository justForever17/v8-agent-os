import { useCallback, useEffect, useMemo, useState } from "react";

import {
    isPhonePreviewBlockedByLoopback,
    resolveRenderableMediaCandidates,
    resolveRenderableMediaUrl,
} from "@/src/lib/workspace-links";
import { useAppSession } from "@/src/providers/app-session";

export function usePreparedPhoneMediaSource({
    src,
    candidates,
    title,
}: {
    src: string;
    candidates?: string[];
    title?: string;
}) {
    const { adminBaseUrl } = useAppSession();
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
            setError(title ? `${title} 暂不可达` : "当前媒体内容暂不可达。");
            return () => {
                cancelled = true;
            };
        }

        if (previewBlocked) {
            setResolvedSrc("");
            setLoading(false);
            setError("当前预览地址仍是本机回环地址，手机端无法直接访问。请改用可达的 Admin 地址后重试。");
            return () => {
                cancelled = true;
            };
        }

        setResolvedSrc(rawCandidate);
        setLoading(false);
        setError("");

        return () => {
            cancelled = true;
        };
    }, [adminBaseUrl, candidateIndex, previewBlocked, rawCandidate, title]);

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
