"use client";
import { useSyncExternalStore } from "react";
let shellVisible = true;
const listeners = new Set<() => void>();
export function setShellSurfaceVisible(visible: boolean) {
    shellVisible = visible;
    listeners.forEach((listener) => listener());
    if (typeof window !== "undefined") window.dispatchEvent(new Event("v8-surface-visibility"));
}
export function isSurfaceVisible() { return typeof document !== "undefined" && document.visibilityState !== "hidden" && shellVisible; }
function subscribe(listener: () => void) {
    listeners.add(listener); document.addEventListener("visibilitychange", listener);
    return () => { listeners.delete(listener); document.removeEventListener("visibilitychange", listener); };
}
export function useSurfaceVisible() { return useSyncExternalStore(subscribe, isSurfaceVisible, () => true); }
