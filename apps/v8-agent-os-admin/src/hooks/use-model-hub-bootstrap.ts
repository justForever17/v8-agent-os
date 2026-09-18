import { useCallback, useEffect, useRef, useState, type SetStateAction } from "react";
import { fetchAdminJson, peekAdminJsonCache } from "@/lib/admin-client-cache";
import {
    AIModel,
    AIProvider,
    AudioRuntimeConfig,
    CatalogProvider,
    mergeAudioConfig,
    ModelHubBootstrapPayload,
    MODEL_HUB_BOOTSTRAP_URL,
    ModelHubPayload,
    preserveModelOrder,
} from "@/lib/model-hub/model-hub-domain";
import type { ConfigRegistryEnvelope } from "@/lib/config-registry";

export function useModelHubBootstrap() {
    const cachedBootstrap = peekAdminJsonCache<ModelHubBootstrapPayload>(MODEL_HUB_BOOTSTRAP_URL);
    const [providers, setProviders] = useState<AIProvider[]>(() => Array.isArray(cachedBootstrap?.providers) ? cachedBootstrap.providers : []);
    const [models, setModels] = useState<AIModel[]>(() => Array.isArray(cachedBootstrap?.models) ? cachedBootstrap.models : []);
    const [hubEnvelope, setHubEnvelope] = useState<ConfigRegistryEnvelope<ModelHubPayload> | null>(() => cachedBootstrap?.hubEnvelope || null);
    const [isLoading, setIsLoading] = useState(() => !cachedBootstrap);
    const [hasLoadedAudioConfig, setHasLoadedAudioConfig] = useState(() => Boolean(cachedBootstrap));
    const [defaultModelRef, setDefaultModelRef] = useState<string | null>(() => {
        const value = cachedBootstrap?.defaultModel || {};
        return value.modelRef || value.modelId || value.value || null;
    });
    const [catalogProviders, setCatalogProviders] = useState<CatalogProvider[]>(() => Array.isArray(cachedBootstrap?.catalog?.providers) ? cachedBootstrap.catalog.providers : []);
    const [audioConfig, setAudioConfig] = useState<AudioRuntimeConfig>(() => mergeAudioConfig(cachedBootstrap?.audioConfig || null));
    const [bootstrapError, setBootstrapError] = useState<string | null>(null);
    const generationRef = useRef(0);
    const audioDraftRevisionRef = useRef(0);

    const updateAudioConfig = useCallback((next: SetStateAction<AudioRuntimeConfig>) => {
        audioDraftRevisionRef.current += 1;
        setAudioConfig(next);
    }, []);

    const fetchData = useCallback(async (force = false, keepModelOrder = false) => {
        const generation = ++generationRef.current;
        const audioRevision = audioDraftRevisionRef.current;
        if (!peekAdminJsonCache(MODEL_HUB_BOOTSTRAP_URL)) setIsLoading(true);
        setBootstrapError(null);
        try {
            const payload = await fetchAdminJson<ModelHubBootstrapPayload>(MODEL_HUB_BOOTSTRAP_URL, { force, ttlMs: 30_000 });
            if (generation !== generationRef.current) return false;
            setProviders(Array.isArray(payload.providers) ? payload.providers : []);
            const nextModels = Array.isArray(payload.models) ? payload.models : [];
            setModels((current) => keepModelOrder ? preserveModelOrder(current, nextModels) : nextModels);
            setHubEnvelope(payload.hubEnvelope || null);
            if (audioDraftRevisionRef.current === audioRevision) {
                setAudioConfig(mergeAudioConfig(payload.audioConfig || null));
            }
            setHasLoadedAudioConfig(true);
            const defaultData = payload.defaultModel || {};
            setDefaultModelRef(defaultData.modelRef || defaultData.modelId || defaultData.value || null);
            const catalogData = payload.catalog || {};
            setCatalogProviders(Array.isArray(catalogData.providers) ? catalogData.providers : []);
            return true;
        } catch (error) {
            if (generation === generationRef.current) {
                setBootstrapError(error instanceof Error ? error.message : String(error || "model_hub_load_failed"));
            }
            return false;
        } finally {
            if (generation === generationRef.current) setIsLoading(false);
        }
    }, []);

    useEffect(() => {
        void fetchData();
    }, [fetchData]);

    return {
        providers,
        models,
        setModels,
        hubEnvelope,
        isLoading,
        hasLoadedAudioConfig,
        audioConfig,
        setAudioConfig: updateAudioConfig,
        bootstrapError,
        defaultModelRef,
        setDefaultModelRef,
        catalogProviders,
        setCatalogProviders,
        fetchData,
    };
}
