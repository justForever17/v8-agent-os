export type MergeableFeaturePackTruth = {
    id: string;
    recommendedOrder: number;
    status: string;
    installed: boolean;
    restartRequired: boolean;
    logRef: string | null;
    lastError: string | null;
    updatedAt: string | null;
};

export function engineFeaturePackSnapshotIsAuthoritative(snapshot: {
    available: boolean | null;
    stale: boolean;
    updatedAt: number;
}) {
    return snapshot.available === true
        && snapshot.stale === false
        && Number.isFinite(snapshot.updatedAt)
        && snapshot.updatedAt > 0;
}

export function mergeFeaturePackTruth<T extends MergeableFeaturePackTruth>(
    configPacks: T[],
    enginePacks: T[] | null,
    engineSnapshotUpdatedAt: number,
) {
    if (!enginePacks) return configPacks;
    const configById = new Map(configPacks.map((pack) => [pack.id, pack]));
    return enginePacks.map((enginePack) => {
        const configPack = configById.get(enginePack.id);
        if (!configPack) return enginePack;
        const configUpdatedAt = Date.parse(configPack.updatedAt || "") || 0;
        if (configUpdatedAt > engineSnapshotUpdatedAt) return configPack;
        return {
            ...configPack,
            ...enginePack,
            logRef: enginePack.logRef ?? configPack.logRef,
            lastError: enginePack.lastError
                ?? (enginePack.status === "failed" ? configPack.lastError : null),
            updatedAt: enginePack.updatedAt ?? configPack.updatedAt,
        } as T;
    }).sort((a, b) => a.recommendedOrder - b.recommendedOrder);
}
