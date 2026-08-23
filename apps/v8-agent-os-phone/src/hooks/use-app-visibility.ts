import { useSyncExternalStore } from "react";
import { AppState, type AppStateStatus } from "react-native";

let visible = AppState.currentState === "active";
let subscription: ReturnType<typeof AppState.addEventListener> | undefined;
const listeners = new Set<() => void>();

function handleAppStateChange(state: AppStateStatus) {
    const nextVisible = state === "active";
    if (visible === nextVisible) return;
    visible = nextVisible;
    listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
    listeners.add(listener);
    if (!subscription) {
        visible = AppState.currentState === "active";
        subscription = AppState.addEventListener("change", handleAppStateChange);
    }
    return () => {
        listeners.delete(listener);
        if (listeners.size === 0) {
            subscription?.remove();
            subscription = undefined;
        }
    };
}

export function useAppVisibility() {
    return useSyncExternalStore(subscribe, () => visible, () => true);
}
