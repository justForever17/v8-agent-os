import { useCallback, useEffect, useRef, useState, type SetStateAction } from "react";
import { fetchAdminJson, peekAdminJsonCache, primeAdminJsonCache } from "@/lib/admin-client-cache";
import { mergeAudioConfig, type AudioRuntimeConfig } from "@/lib/model-hub/audio";
import type { CatalogProvider } from "@/lib/model-hub/catalog";
import { preserveModelOrder } from "@/lib/model-hub/models";
import type { AIModel, ModelHubBootstrapPayload } from "@/lib/model-hub/types";

const BOOTSTRAP_URL = "/api/model-hub/bootstrap";

function updateBootstrapCache(update: (current: ModelHubBootstrapPayload) => ModelHubBootstrapPayload) {
    // Priming replaces the old request identity/promise, so a pre-write read
    // cannot refill this cache or be reused after the page remounts.
    // Keep the saved facts visible while the next visit revalidates the rest.
    primeAdminJsonCache(BOOTSTRAP_URL, update(peekAdminJsonCache<ModelHubBootstrapPayload>(BOOTSTRAP_URL) ?? {}), 0);
}

function snapshotFrom(payload?: ModelHubBootstrapPayload) {
    return {
        providers: Array.isArray(payload?.providers) ? payload.providers : [],
        models: Array.isArray(payload?.models) ? payload.models : [],
        hubEnvelope: payload?.hubEnvelope ?? null,
        catalogProviders: Array.isArray(payload?.catalog?.providers) ? payload.catalog.providers : [],
        defaultModelRef: payload?.defaultModel?.modelRef || null,
        audioConfig: payload?.audioConfig ? mergeAudioConfig(payload.audioConfig) : null,
    };
}

export function useModelHubBootstrap() {
    const [snapshot, setSnapshot] = useState(() => snapshotFrom(peekAdminJsonCache<ModelHubBootstrapPayload>(BOOTSTRAP_URL)));
    const [isLoading, setIsLoading] = useState(() => !peekAdminJsonCache(BOOTSTRAP_URL));
    const [bootstrapError, setBootstrapError] = useState<string | null>(null);
    const [audioDraft, setAudioDraft] = useState<AudioRuntimeConfig | null>(null);
    const generation = useRef(0);
    const mounted = useRef(true);

    const refresh = useCallback(async (force = false, keepModelOrder = false) => {
        if (!mounted.current) return false;
        const request = ++generation.current;
        if (!peekAdminJsonCache(BOOTSTRAP_URL)) setIsLoading(true);
        setBootstrapError(null);
        try {
            const payload = await fetchAdminJson<ModelHubBootstrapPayload>(BOOTSTRAP_URL, { force, ttlMs: 30_000 });
            if (!mounted.current || request !== generation.current) return false;
            const next = snapshotFrom(payload);
            setSnapshot(current => ({ ...next, models: keepModelOrder ? preserveModelOrder(current.models, next.models) : next.models }));
            return true;
        } catch (error) {
            if (mounted.current && request === generation.current) {
                setBootstrapError(error instanceof Error ? error.message : String(error || "model_hub_load_failed"));
            }
            return false;
        } finally {
            if (mounted.current && request === generation.current) setIsLoading(false);
        }
    }, []);

    useEffect(() => {
        mounted.current = true;
        void refresh();
        return () => { mounted.current = false; generation.current += 1; };
    }, [refresh]);

    const updateAudio = useCallback((next: SetStateAction<AudioRuntimeConfig>) => {
        setAudioDraft(current => typeof next === "function"
            ? next(current ?? snapshot.audioConfig ?? mergeAudioConfig(null)) : next);
    }, [snapshot.audioConfig]);

    const acceptSavedAudio = useCallback((submitted: AudioRuntimeConfig, saved: AudioRuntimeConfig) => {
        if (!mounted.current) return;
        // A read started before this write is no longer an admissible snapshot.
        generation.current += 1;
        setIsLoading(false);
        updateBootstrapCache(current => ({ ...current, audioConfig: saved }));
        setSnapshot(current => ({ ...current, audioConfig: saved }));
        setAudioDraft(current => current === submitted ? null : current);
    }, []);

    const removeModel = useCallback((model: Pick<AIModel, "id" | "providerId">) => {
        if (!mounted.current) return;
        generation.current += 1;
        setIsLoading(false);
        updateBootstrapCache(current => ({ ...current, models: current.models?.filter(item => !(item.id === model.id && item.providerId === model.providerId)) }));
        setSnapshot(current => ({ ...current, models: current.models.filter(item => !(item.id === model.id && item.providerId === model.providerId)) }));
    }, []);

    const rememberCatalogProvider = useCallback((provider: CatalogProvider) => {
        if (!mounted.current) return;
        generation.current += 1;
        setIsLoading(false);
        updateBootstrapCache(current => ({ ...current, catalog: { ...current.catalog, providers: [provider, ...(current.catalog?.providers ?? []).filter(item => item.id !== provider.id)] } }));
        setSnapshot(current => ({ ...current, catalogProviders: [provider, ...current.catalogProviders.filter(item => item.id !== provider.id)] }));
    }, []);

    const setDefaultModel = useCallback((modelRef: string) => {
        if (!mounted.current) return;
        generation.current += 1;
        setIsLoading(false);
        updateBootstrapCache(current => ({ ...current, defaultModel: { modelRef } }));
        setSnapshot(current => ({ ...current, defaultModelRef: modelRef }));
    }, []);

    return {
        snapshot, isLoading, bootstrapError, refresh, removeModel, rememberCatalogProvider, setDefaultModel,
        audio: {
            value: audioDraft ?? snapshot.audioConfig ?? mergeAudioConfig(null),
            loaded: snapshot.audioConfig !== null,
            update: updateAudio,
            acceptSaved: acceptSavedAudio,
        },
    };
}
