"use client";
import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";
import { flushDraft, hydrateDraft, readDraft, setDraftField, subscribeDraft } from "@/lib/composer-drafts";

export function useComposerDraft(key: string) {
    const subscribe = useCallback((listener: () => void) => subscribeDraft(key, listener), [key]);
    const snapshot = useCallback(() => readDraft(key), [key]);
    const record = useSyncExternalStore(subscribe, snapshot, snapshot);
    useEffect(() => {
        void hydrateDraft(key);
        const flush = () => { void flushDraft(key); };
        window.addEventListener("pagehide", flush);
        document.addEventListener("visibilitychange", flush);
        return () => { flush(); window.removeEventListener("pagehide", flush); document.removeEventListener("visibilitychange", flush); };
    }, [key]);
    return record;
}
export function useDraftField<T>(key: string, field: string, initial: T): [T, (value: T | ((previous: T) => T)) => void] {
    const initialRef = useRef(initial);
    const subscribe = useCallback((listener: () => void) => subscribeDraft(key, listener), [key]);
    const snapshot = useCallback(() => (readDraft(key).values[field] ?? initialRef.current) as T, [key, field]);
    const value = useSyncExternalStore(subscribe, snapshot, snapshot);
    const setValue = useCallback((next: T | ((previous: T) => T)) => setDraftField(key, field, next, initialRef.current), [key, field]);
    return [value, setValue];
}
