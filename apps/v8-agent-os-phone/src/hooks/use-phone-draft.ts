import { useCallback, useEffect, useMemo, useSyncExternalStore, type Dispatch, type SetStateAction } from "react";
import { phoneDrafts } from "@/src/lib/phone-drafts";

export function usePhoneDraftField<T>(key: string, field: string, initial: T | (() => T)): [T, Dispatch<SetStateAction<T>>] {
    // Every setter captures the original session key. Async uploads or queue edits
    // completing after navigation update their own draft, never the visible one.
    const fallback = useMemo(() => typeof initial === "function" ? (initial as () => T)() : initial, [key]);
    const subscribe = useCallback((listener: () => void) => phoneDrafts.subscribe(key, listener), [key]);
    const get = useCallback(() => (phoneDrafts.get(key).values[field] as T | undefined) ?? fallback, [key, field, fallback]);
    const value = useSyncExternalStore(subscribe, get, get);
    useEffect(() => { void phoneDrafts.hydrate(key).catch(() => undefined); }, [key]);
    const set = useCallback<Dispatch<SetStateAction<T>>>((next) => phoneDrafts.set(key, field, next, fallback), [key, field, fallback]);
    return [value, set];
}

export function usePhoneDraftStatus(key: string) {
    const subscribe = useCallback((listener: () => void) => phoneDrafts.subscribe(key, listener), [key]);
    const get = useCallback(() => phoneDrafts.get(key), [key]);
    return useSyncExternalStore(subscribe, get, get);
}
