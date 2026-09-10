import { useCallback, useSyncExternalStore } from "react";

const debugPreferenceKey = "v8-admin-debug-mode";
const subscribeDebugPreference = (onChange: () => void) => {
    const handleStorage = (event: StorageEvent) => {
        if (event.key === debugPreferenceKey) onChange();
    };
    const handleCustom = () => onChange();
    window.addEventListener("storage", handleStorage);
    window.addEventListener("v8-debug-mode-change", handleCustom);
    return () => {
        window.removeEventListener("storage", handleStorage);
        window.removeEventListener("v8-debug-mode-change", handleCustom);
    };
};
const readDebugPreference = () => localStorage.getItem(debugPreferenceKey) === "true";
const readServerDebugPreference = () => false;

export function useDebugMode() {
    // Match server HTML on the first render, then subscribe to the browser preference.
    const debugMode = useSyncExternalStore(subscribeDebugPreference, readDebugPreference, readServerDebugPreference);

    const toggleDebugMode = useCallback((enabled: boolean) => {
        if (typeof window === "undefined") {
            return;
        }
        localStorage.setItem(debugPreferenceKey, String(enabled));
        window.dispatchEvent(new Event("v8-debug-mode-change"));
    }, []);

    return [debugMode, toggleDebugMode] as const;
}
