import { useCallback, useEffect, useLayoutEffect, useRef } from "react";

import { phoneDrafts } from "@/src/lib/phone-drafts";

export type PhoneConversationLoadOptions = {
    force?: boolean;
    token?: number;
    replaceTranscript?: boolean;
};

type RealtimeSession = {
    startRealtime: (conversationId: string, token?: number) => Promise<void>;
    stopRealtime: (options?: { preserveMessageState?: boolean }) => void;
    isRealtimeActive: (conversationId: string) => boolean;
};

type PhoneConversationLifecycleOptions = {
    identityKey: string;
    status: string;
    activeConversationId: string | null;
    draftKey: string;
    isFocused: boolean;
    appVisible: boolean;
    realtime: RealtimeSession;
    hydrate: (conversationId: string, options?: PhoneConversationLoadOptions) => Promise<boolean>;
    resetView: (preserveMessages: boolean) => void;
};

/** Owns view transitions and hydration leases; transport remains in the realtime hook. */
export function usePhoneConversationLifecycle(options: PhoneConversationLifecycleOptions) {
    const latest = useRef(options);
    latest.current = options;
    const activeConversationIdRef = useRef(options.activeConversationId);
    const conversationTransitionTokenRef = useRef(0);
    const previousScope = useRef<{ identityKey: string; sessionId: string | null } | null>(null);
    const seededSession = useRef<string | null>(null);
    const hydratedSession = useRef<string | null>(null);
    const hydration = useRef<object | null>(null);

    const invalidate = useCallback(() => {
        conversationTransitionTokenRef.current += 1;
        seededSession.current = null;
        hydratedSession.current = null;
        hydration.current = null;
        latest.current.realtime.stopRealtime();
    }, []);

    const seedConversation = useCallback((sessionId: string) => {
        invalidate();
        activeConversationIdRef.current = sessionId;
        seededSession.current = sessionId;
    }, [invalidate]);

    const isSeeded = useCallback((sessionId: string) => seededSession.current === sessionId, []);

    const beginHydration = useCallback((sessionId: string, settings?: PhoneConversationLoadOptions) => {
        const scope = latest.current;
        const token = settings?.token ?? conversationTransitionTokenRef.current;
        const current = () => token === conversationTransitionTokenRef.current
            && activeConversationIdRef.current === sessionId
            && latest.current.identityKey === scope.identityKey;
        if (!current() || (!settings?.force && (hydration.current || hydratedSession.current === sessionId))) return null;
        const request = {};
        const previouslyHydrated = hydratedSession.current === sessionId;
        hydration.current = request;
        return {
            token,
            previouslyHydrated,
            isCurrent: () => current() && hydration.current === request,
            markHydrated: () => { if (current() && hydration.current === request) hydratedSession.current = sessionId; },
            finish: () => { if (hydration.current === request) hydration.current = null; },
        };
    }, []);

    // Fence old callbacks before passive effects and before a same-id owner rehydrates.
    useLayoutEffect(() => {
        activeConversationIdRef.current = options.activeConversationId;
        conversationTransitionTokenRef.current += 1;
        hydration.current = null;
        return () => {
            conversationTransitionTokenRef.current += 1;
            hydration.current = null;
        };
    }, [options.activeConversationId, options.identityKey, options.status, options.isFocused, options.appVisible]);

    useEffect(() => {
        const { status, activeConversationId, identityKey, draftKey, isFocused, appVisible, realtime } = options;
        const flushDraft = () => { void phoneDrafts.flush(draftKey).catch(() => undefined); };
        if (!isFocused || !appVisible) {
            realtime.stopRealtime();
            flushDraft();
            return;
        }
        if (status !== "authenticated" || !activeConversationId) {
            invalidate();
            previousScope.current = null;
            latest.current.resetView(false);
            return flushDraft;
        }

        const previous = previousScope.current;
        const ownerChanged = previous !== null && previous.identityKey !== identityKey;
        const conversationChanged = ownerChanged || previous?.sessionId !== activeConversationId;
        previousScope.current = { identityKey, sessionId: activeConversationId };
        const token = conversationTransitionTokenRef.current;
        const skipInitialHydration = !ownerChanged && conversationChanged && seededSession.current === activeConversationId;
        if (conversationChanged) {
            hydratedSession.current = null;
            realtime.stopRealtime({ preserveMessageState: skipInitialHydration });
            latest.current.resetView(skipInitialHydration);
        }
        seededSession.current = null;

        let cancelled = false;
        const isCurrent = () => !cancelled && conversationTransitionTokenRef.current === token
            && activeConversationIdRef.current === activeConversationId && latest.current.identityKey === identityKey;
        void (async () => {
            if (skipInitialHydration) {
                if (isCurrent()) await realtime.startRealtime(activeConversationId, token);
                return;
            }
            const loaded = await latest.current.hydrate(activeConversationId, { force: true, token });
            if (isCurrent() && (loaded || !realtime.isRealtimeActive(activeConversationId))) {
                await realtime.startRealtime(activeConversationId, token);
            }
        })();
        return () => {
            cancelled = true;
            realtime.stopRealtime();
            flushDraft();
        };
    }, [options.activeConversationId, options.identityKey, options.draftKey, options.status, options.isFocused, options.appVisible, invalidate]);

    return {
        // Read-only views allow existing asynchronous consumers to check a captured token.
        activeConversationIdRef: activeConversationIdRef as Readonly<{ current: string | null }>,
        conversationTransitionTokenRef: conversationTransitionTokenRef as Readonly<{ current: number }>,
        invalidate,
        seedConversation,
        isSeeded,
        beginHydration,
    };
}
