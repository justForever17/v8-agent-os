export interface CanvasGraphSessionIdentity {
    sessionId: string;
    endpoint: string;
    storageKey: string;
    legacyStorageKey: string;
}

export interface CanvasGraphSaveSeed {
    revision: number;
    lastSavedKey: string;
    persisted: boolean;
    migrationPending: boolean;
}

export interface CanvasGraphSaveLaneState extends CanvasGraphSaveSeed {
    saving: boolean;
    dirty: boolean;
    pendingKey: string;
    inFlightKey: string;
    lastError: unknown;
}

export interface CanvasGraphSaveRequest<TGraph> extends CanvasGraphSessionIdentity {
    graph: TGraph;
    persistenceKey: string;
    expectedRevision: number;
    signal: AbortSignal;
}

export interface CanvasGraphSaveResult<TMeta> {
    accepted: boolean;
    revision: number;
    persistenceKey?: string;
    meta: TMeta;
}

interface PendingSave<TGraph> {
    graph: TGraph;
    persistenceKey: string;
}

interface SaveWaiter {
    resolve: (saved: boolean) => void;
    reject: (reason: unknown) => void;
}

interface SaveLane<TGraph> extends CanvasGraphSaveLaneState {
    pending: PendingSave<TGraph> | null;
    desired: PendingSave<TGraph> | null;
    settled: CanvasGraphSettledSave<TGraph, unknown> | null;
    waiters: Map<string, SaveWaiter[]>;
    controller: AbortController | null;
    retryTimer: unknown;
    retryAttempt: number;
}

export interface CanvasGraphSettledSave<TGraph, TMeta> {
    graph: TGraph;
    result: CanvasGraphSaveResult<TMeta>;
}

export interface CanvasGraphDesiredSave<TGraph> {
    graph: TGraph;
    persistenceKey: string;
}

export interface CanvasGraphSaveSchedulerOptions<TGraph, TMeta> {
    persistenceKeyOf: (graph: TGraph) => string;
    save: (request: CanvasGraphSaveRequest<TGraph>) => Promise<CanvasGraphSaveResult<TMeta>>;
    onResult?: (
        identity: CanvasGraphSessionIdentity,
        graph: TGraph,
        result: CanvasGraphSaveResult<TMeta>,
    ) => void;
    onError?: (identity: CanvasGraphSessionIdentity, reason: unknown) => void;
    onLaneState?: (identity: CanvasGraphSessionIdentity, state: CanvasGraphSaveLaneState) => void;
    retryDelaysMs?: readonly number[];
    setTimer?: (callback: () => void, delayMs: number) => unknown;
    clearTimer?: (timer: unknown) => void;
}

export type CanvasGraphSaveSchedulerCallbacks<TGraph, TMeta> = Pick<
    CanvasGraphSaveSchedulerOptions<TGraph, TMeta>,
    "onResult" | "onError" | "onLaneState"
>;

export interface CanvasGraphSaveScheduler<TGraph, TMeta = unknown> {
    setCallbacks: (callbacks: CanvasGraphSaveSchedulerCallbacks<TGraph, TMeta>) => () => void;
    ensureSession: (sessionId: string, seed: CanvasGraphSaveSeed) => CanvasGraphSaveLaneState;
    configureSession: (sessionId: string, seed: CanvasGraphSaveSeed) => CanvasGraphSaveLaneState;
    getState: (sessionId: string) => CanvasGraphSaveLaneState | null;
    getDesired: (sessionId: string) => CanvasGraphDesiredSave<TGraph> | null;
    getSettled: (sessionId: string) => CanvasGraphSettledSave<TGraph, TMeta> | null;
    enqueue: (sessionId: string, graph: TGraph) => Promise<boolean>;
    flush: (sessionId: string, graph: TGraph) => CanvasGraphSaveLaneState;
    retry: (sessionId: string) => void;
    dispose: () => void;
}

const DEFAULT_RETRY_DELAYS_MS = [750, 2_000, 5_000, 15_000] as const;

export function canvasGraphSessionIdentity(sessionId: string): CanvasGraphSessionIdentity {
    return {
        sessionId,
        endpoint: `/api/workbench/sessions/${encodeURIComponent(sessionId)}/canvas/graph`,
        storageKey: `v8-web-creative-canvas:v3:${sessionId}`,
        legacyStorageKey: `v8-web-creative-canvas:v2:${sessionId}`,
    };
}

function publicLaneState<TGraph>(lane: SaveLane<TGraph>): CanvasGraphSaveLaneState {
    return {
        revision: lane.revision,
        lastSavedKey: lane.lastSavedKey,
        persisted: lane.persisted,
        migrationPending: lane.migrationPending,
        saving: lane.saving,
        dirty: lane.dirty,
        pendingKey: lane.pending?.persistenceKey || "",
        inFlightKey: lane.inFlightKey,
        lastError: lane.lastError,
    };
}

export function createCanvasGraphSaveScheduler<TGraph, TMeta>(
    options: CanvasGraphSaveSchedulerOptions<TGraph, TMeta>,
): CanvasGraphSaveScheduler<TGraph, TMeta> {
    const lanes = new Map<string, SaveLane<TGraph>>();
    const retryDelays = options.retryDelaysMs?.length ? options.retryDelaysMs : DEFAULT_RETRY_DELAYS_MS;
    const setTimer = options.setTimer || ((callback: () => void, delayMs: number) => window.setTimeout(callback, delayMs));
    const clearTimer = options.clearTimer || ((timer: unknown) => window.clearTimeout(timer as number));
    let callbacks: CanvasGraphSaveSchedulerCallbacks<TGraph, TMeta> = {
        onResult: options.onResult,
        onError: options.onError,
        onLaneState: options.onLaneState,
    };
    let disposed = false;
    let callbackOwner = 0;

    const identityFor = (sessionId: string) => canvasGraphSessionIdentity(sessionId);

    const notify = (sessionId: string, lane: SaveLane<TGraph>) => {
        if (!disposed) callbacks.onLaneState?.(identityFor(sessionId), publicLaneState(lane));
    };

    const makeLane = (seed: CanvasGraphSaveSeed): SaveLane<TGraph> => ({
        ...seed,
        revision: Math.max(0, Number(seed.revision) || 0),
        saving: false,
        dirty: false,
        pendingKey: "",
        inFlightKey: "",
        lastError: null,
        pending: null,
        desired: null,
        settled: null,
        waiters: new Map(),
        controller: null,
        retryTimer: null,
        retryAttempt: 0,
    });

    const ensureSession = (sessionId: string, seed: CanvasGraphSaveSeed) => {
        let lane = lanes.get(sessionId);
        if (!lane) {
            lane = makeLane(seed);
            lanes.set(sessionId, lane);
        }
        return publicLaneState(lane);
    };

    const settleWaiters = (lane: SaveLane<TGraph>, persistenceKey: string, saved: boolean, reason?: unknown) => {
        const waiters = lane.waiters.get(persistenceKey) || [];
        lane.waiters.delete(persistenceKey);
        for (const waiter of waiters) {
            if (reason !== undefined) waiter.reject(reason);
            else waiter.resolve(saved);
        }
    };

    const scheduleRetry = (sessionId: string, lane: SaveLane<TGraph>, drain: (sessionId: string) => void) => {
        if (disposed || lane.retryTimer !== null || !lane.pending) return;
        const delay = retryDelays[Math.min(lane.retryAttempt, retryDelays.length - 1)] || 15_000;
        lane.retryAttempt += 1;
        lane.retryTimer = setTimer(() => {
            lane.retryTimer = null;
            drain(sessionId);
        }, delay);
    };

    const drain = (sessionId: string) => {
        const lane = lanes.get(sessionId);
        if (!lane || disposed || lane.saving || !lane.pending) return;
        const pending = lane.pending;
        lane.pending = null;
        if (lane.persisted && !lane.migrationPending && pending.persistenceKey === lane.lastSavedKey) {
            if (lane.desired?.persistenceKey === pending.persistenceKey) lane.desired = null;
            lane.dirty = Boolean(lane.pending);
            settleWaiters(lane, pending.persistenceKey, true);
            notify(sessionId, lane);
            if (lane.pending) drain(sessionId);
            return;
        }

        lane.saving = true;
        lane.dirty = true;
        lane.inFlightKey = pending.persistenceKey;
        lane.lastError = null;
        lane.controller = new AbortController();
        notify(sessionId, lane);
        const identity = identityFor(sessionId);
        const expectedRevision = lane.revision;
        void options.save({
            ...identity,
            graph: pending.graph,
            persistenceKey: pending.persistenceKey,
            expectedRevision,
            signal: lane.controller.signal,
        }).then((result) => {
            if (disposed) return;
            const resultRevision = Math.max(0, Number(result.revision) || expectedRevision + 1);
            const staleResult = resultRevision < lane.revision;
            const latestSameGraph = lane.desired?.persistenceKey === pending.persistenceKey
                ? lane.desired.graph
                : pending.graph;
            if (!staleResult) {
                lane.revision = resultRevision;
                lane.persisted = true;
                lane.migrationPending = false;
                lane.lastSavedKey = result.persistenceKey || (result.accepted ? pending.persistenceKey : lane.lastSavedKey);
                lane.retryAttempt = 0;
                lane.lastError = null;
                lane.settled = { graph: latestSameGraph, result } as CanvasGraphSettledSave<TGraph, unknown>;
                notify(sessionId, lane);
                try {
                    callbacks.onResult?.(identity, latestSameGraph, result);
                } catch (reason) {
                    callbacks.onError?.(identity, reason);
                }
            }
            settleWaiters(lane, pending.persistenceKey, staleResult ? false : result.accepted);
            if (!lane.pending && lane.desired?.persistenceKey === pending.persistenceKey) lane.desired = null;
        }).catch((reason) => {
            if (disposed) return;
            if (!lane.pending) lane.pending = pending;
            lane.lastError = reason;
            lane.dirty = true;
            settleWaiters(lane, pending.persistenceKey, false, reason);
            callbacks.onError?.(identity, reason);
        }).finally(() => {
            lane.saving = false;
            lane.inFlightKey = "";
            lane.controller = null;
            lane.dirty = Boolean(lane.pending)
                || lane.migrationPending;
            notify(sessionId, lane);
            if (disposed) return;
            if (lane.lastError) scheduleRetry(sessionId, lane, drain);
            else if (lane.pending) drain(sessionId);
        });
    };

    const configureSession = (sessionId: string, seed: CanvasGraphSaveSeed) => {
        const lane = lanes.get(sessionId) || makeLane(seed);
        if (!lanes.has(sessionId)) lanes.set(sessionId, lane);
        const revision = Math.max(0, Number(seed.revision) || 0);
        const serverAdvanced = revision > lane.revision;
        if (revision >= lane.revision) {
            lane.revision = revision;
            lane.lastSavedKey = seed.lastSavedKey;
            lane.persisted = seed.persisted;
            lane.migrationPending = seed.migrationPending;
        }
        if (serverAdvanced && lane.saving && !lane.pending && lane.desired?.persistenceKey === lane.inFlightKey) {
            lane.desired = null;
        }
        lane.dirty = Boolean(lane.pending || lane.inFlightKey)
            || lane.migrationPending;
        notify(sessionId, lane);
        return publicLaneState(lane);
    };

    const queue = (sessionId: string, graph: TGraph, waiter?: SaveWaiter) => {
        if (disposed) {
            const reason = new Error("Canvas graph save scheduler is disposed");
            if (waiter) waiter.reject(reason);
            else throw reason;
            return null;
        }
        const lane = lanes.get(sessionId) || makeLane({
            revision: 0,
            lastSavedKey: "",
            persisted: false,
            migrationPending: false,
        });
        if (!lanes.has(sessionId)) lanes.set(sessionId, lane);
        const persistenceKey = options.persistenceKeyOf(graph);
        lane.desired = { graph, persistenceKey };
        if (lane.persisted && !lane.migrationPending && persistenceKey === lane.lastSavedKey && !lane.pending && !lane.inFlightKey) {
            lane.desired = null;
            waiter?.resolve(true);
            return publicLaneState(lane);
        }
        if (waiter) lane.waiters.set(persistenceKey, [...(lane.waiters.get(persistenceKey) || []), waiter]);
        if (lane.pending && lane.pending.persistenceKey !== persistenceKey) {
            settleWaiters(lane, lane.pending.persistenceKey, false);
            lane.pending = null;
        }
        if (persistenceKey !== lane.inFlightKey) {
            lane.pending = { graph, persistenceKey };
        }
        lane.dirty = true;
        lane.lastError = null;
        if (lane.retryTimer !== null) {
            clearTimer(lane.retryTimer);
            lane.retryTimer = null;
        }
        notify(sessionId, lane);
        drain(sessionId);
        return publicLaneState(lane);
    };

    const enqueue = (sessionId: string, graph: TGraph) => new Promise<boolean>((resolve, reject) => {
        queue(sessionId, graph, { resolve, reject });
    });

    const flush = (sessionId: string, graph: TGraph) => {
        // The lane owns retry and error state after its UI detaches, so no caller promise can be orphaned.
        return queue(sessionId, graph) as CanvasGraphSaveLaneState;
    };

    const retry = (sessionId: string) => {
        const lane = lanes.get(sessionId);
        if (!lane || disposed) return;
        if (lane.retryTimer !== null) {
            clearTimer(lane.retryTimer);
            lane.retryTimer = null;
        }
        lane.lastError = null;
        drain(sessionId);
    };

    const dispose = () => {
        if (disposed) return;
        disposed = true;
        const reason = new Error("Canvas graph save scheduler disposed");
        for (const lane of lanes.values()) {
            if (lane.retryTimer !== null) clearTimer(lane.retryTimer);
            lane.controller?.abort();
            for (const persistenceKey of lane.waiters.keys()) {
                settleWaiters(lane, persistenceKey, false, reason);
            }
        }
        lanes.clear();
    };

    return {
        setCallbacks: (nextCallbacks) => {
            const owner = callbackOwner + 1;
            callbackOwner = owner;
            callbacks = { ...nextCallbacks };
            return () => {
                if (callbackOwner === owner) callbacks = {};
            };
        },
        ensureSession,
        configureSession,
        getState: (sessionId) => {
            const lane = lanes.get(sessionId);
            return lane ? publicLaneState(lane) : null;
        },
        getDesired: (sessionId) => {
            const desired = lanes.get(sessionId)?.desired;
            return desired ? { ...desired } : null;
        },
        getSettled: (sessionId) => {
            const settled = lanes.get(sessionId)?.settled;
            return settled ? settled as CanvasGraphSettledSave<TGraph, TMeta> : null;
        },
        enqueue,
        flush,
        retry,
        dispose,
    };
}
