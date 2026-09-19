import { useCallback, useLayoutEffect, useRef } from "react";

const SNAPSHOT_GRACE_MS = 8_000;
const SNAPSHOT_DEBOUNCE_MS = 2_400;
const SNAPSHOT_FORCE_DEBOUNCE_MS = 900;

export type PhoneRealtimeSnapshotSync = {
    messages: unknown[];
    deletions: string[];
    syncCursor: string;
    sessionId: string;
};

type PhoneRealtimeSnapshotPort<TSnapshot> = {
    getSyncCursor: (conversationId: string) => Promise<string | null | undefined>;
    fetchSnapshot: (conversationId: string, signal: AbortSignal) => Promise<TSnapshot>;
    syncTimeline: (conversationId: string, cursor: string, signal: AbortSignal) => Promise<PhoneRealtimeSnapshotSync>;
    persistSync: (conversationId: string, result: PhoneRealtimeSnapshotSync, isCurrent: () => boolean) => Promise<void>;
    applySnapshot: (snapshot: TSnapshot) => void;
    onFetched?: (snapshot: TSnapshot, elapsedMs: number) => void;
};

type PhoneRealtimeSessionOptions<TSnapshot> = {
    identityKey: string;
    activeConversationId: string | null;
    enabled?: boolean;
    authorizedRealtimeStream: (
        path: string,
        onEvent: (eventName: string, payload: unknown) => void,
        signal?: AbortSignal,
    ) => Promise<void>;
    isCurrentTransition: (conversationId: string, transitionToken?: number) => boolean;
    onEvent: (eventName: string, payload: unknown) => void;
    shouldKeepAlive: () => boolean;
    resetMessageState: () => void;
    snapshot: PhoneRealtimeSnapshotPort<TSnapshot>;
};

function waitForReconnect(delayMs: number, signal: AbortSignal) {
    if (signal.aborted) return Promise.resolve();
    return new Promise<void>((resolve) => {
        const finish = () => {
            clearTimeout(timer);
            signal.removeEventListener("abort", finish);
            resolve();
        };
        const timer = setTimeout(finish, delayMs);
        signal.addEventListener("abort", finish, { once: true });
    });
}

/** Owns transport cancellation, reconnects and coalesced snapshot recovery. */
export function usePhoneRealtimeSession<TSnapshot>(options: PhoneRealtimeSessionOptions<TSnapshot>) {
    const latest = useRef(options);
    latest.current = options;
    const generation = useRef(0);
    const stream = useRef<{ conversationId: string; controller: AbortController } | null>(null);
    const timer = useRef<{ handle: ReturnType<typeof setTimeout>; force: boolean } | null>(null);
    const inflight = useRef<AbortController | null>(null);
    const pending = useRef<boolean | null>(null);
    const lastSnapshotAppliedAt = useRef(0);

    const stopRealtime = useCallback((settings?: { preserveMessageState?: boolean }) => {
        generation.current += 1;
        if (timer.current) clearTimeout(timer.current.handle);
        timer.current = null;
        pending.current = null;
        inflight.current?.abort();
        inflight.current = null;
        stream.current?.controller.abort();
        stream.current = null;
        lastSnapshotAppliedAt.current = 0;
        if (!settings?.preserveMessageState) latest.current.resetMessageState();
    }, []);

    // Cleanup also invalidates work when a profile changes but reuses a session id.
    useLayoutEffect(() => {
        stopRealtime({ preserveMessageState: true });
        return () => stopRealtime({ preserveMessageState: true });
    }, [options.identityKey, options.activeConversationId, options.enabled, stopRealtime]);

    const isRealtimeActive = useCallback((conversationId: string) => (
        stream.current?.conversationId === conversationId && !stream.current.controller.signal.aborted
    ), []);

    const markSnapshotApplied = useCallback(() => {
        lastSnapshotAppliedAt.current = Date.now();
    }, []);

    const scheduleSnapshotRefresh = useCallback((conversationId?: string | null, settings?: { force?: boolean }) => {
        const scope = latest.current;
        const target = String(conversationId || scope.activeConversationId || "").trim();
        if (!target || scope.activeConversationId !== target || scope.enabled === false) return;
        const force = settings?.force === true;
        if (!force && Date.now() - lastSnapshotAppliedAt.current < SNAPSHOT_GRACE_MS) return;
        if (inflight.current) {
            pending.current = force || pending.current === true;
            return;
        }
        if (timer.current) {
            if (!force || timer.current.force) return;
            clearTimeout(timer.current.handle);
        }

        const epoch = generation.current;
        const scheduledAt = Date.now();
        const isCurrent = () => generation.current === epoch
            && latest.current.identityKey === scope.identityKey
            && latest.current.activeConversationId === target
            && latest.current.enabled !== false;

        const refresh = async () => {
            if (!isCurrent()) return;
            if (!force && (lastSnapshotAppliedAt.current > scheduledAt
                || Date.now() - lastSnapshotAppliedAt.current < SNAPSHOT_GRACE_MS)) return;
            const controller = new AbortController();
            inflight.current = controller;
            const port = scope.snapshot;
            const startedAt = Date.now();
            try {
                const cursor = await port.getSyncCursor(target);
                if (!isCurrent()) return;
                const [snapshot, sync] = await Promise.all([
                    port.fetchSnapshot(target, controller.signal),
                    cursor ? port.syncTimeline(target, cursor, controller.signal) : Promise.resolve(null),
                ]);
                if (!isCurrent()) return;
                if (sync) await port.persistSync(target, sync, isCurrent);
                if (!isCurrent()) return;
                port.onFetched?.(snapshot, Date.now() - startedAt);
                port.applySnapshot(snapshot);
            } catch (error) {
                if (isCurrent() && !controller.signal.aborted) console.warn("[phone] realtime snapshot refresh failed:", error);
            } finally {
                // An old task cannot release the next generation's in-flight slot.
                if (isCurrent() && inflight.current === controller) {
                    inflight.current = null;
                    const followup = pending.current;
                    pending.current = null;
                    if (followup !== null) scheduleSnapshotRefresh(target, { force: followup });
                }
            }
        };

        const scheduled = {
            force,
            handle: setTimeout(() => {
                if (timer.current !== scheduled || !isCurrent()) return;
                timer.current = null;
                void refresh();
            }, force ? SNAPSHOT_FORCE_DEBOUNCE_MS : SNAPSHOT_DEBOUNCE_MS),
        };
        timer.current = scheduled;
    }, []);

    const startRealtime = useCallback(async (conversationId: string, transitionToken?: number) => {
        const scope = latest.current;
        if (scope.enabled === false || scope.activeConversationId !== conversationId
            || !scope.isCurrentTransition(conversationId, transitionToken) || isRealtimeActive(conversationId)) return;
        stopRealtime({ preserveMessageState: true });
        const epoch = generation.current;
        const controller = new AbortController();
        const connection = { conversationId, controller };
        stream.current = connection;
        const isCurrent = () => !controller.signal.aborted && generation.current === epoch
            && latest.current.identityKey === scope.identityKey
            && latest.current.activeConversationId === conversationId && latest.current.enabled !== false
            && latest.current.isCurrentTransition(conversationId, transitionToken);
        let reconnectAttempt = 0;
        try {
            while (isCurrent()) {
                try {
                    await scope.authorizedRealtimeStream(
                        `/api/client/realtime/sessions/${encodeURIComponent(conversationId)}/stream?surface=phone&compact=1`,
                        (eventName, payload) => { if (isCurrent()) latest.current.onEvent(eventName, payload); },
                        controller.signal,
                    );
                } catch (error) {
                    if (isCurrent()) console.warn("[phone] realtime stream stopped:", error);
                }
                if (!isCurrent()) break;
                scheduleSnapshotRefresh(conversationId, { force: true });
                if (!latest.current.shouldKeepAlive()) break;
                await waitForReconnect(Math.min(800 + ++reconnectAttempt * 600, 3200), controller.signal);
            }
        } finally {
            if (stream.current === connection) stream.current = null;
        }
    }, [isRealtimeActive, scheduleSnapshotRefresh, stopRealtime]);

    return { startRealtime, stopRealtime, scheduleSnapshotRefresh, isRealtimeActive, markSnapshotApplied };
}
