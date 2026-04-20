import { useCallback, useEffect, useMemo, useState } from "react";
import { translateCurrent } from "@/src/lib/locale";

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
            setError(title
                ? translateCurrent("src.lib.phone_media_source.temporarily_unreachable_with_title", { title })
                : translateCurrent("src.lib.phone_media_source.temporarily_unreachable"));
            return () => {
                cancelled = true;
            };
        }

        if (previewBlocked) {
            setResolvedSrc("");
            setLoading(false);
            setError(translateCurrent("src.lib.phone_media_source.admin"));
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
