import { useState, useEffect } from "react";

export function useDebugMode() {
    const [debugMode, setDebugMode] = useState(false);

    useEffect(() => {
        setDebugMode(localStorage.getItem("v8-admin-debug-mode") === "true");

        const handleStorage = (e: StorageEvent) => {
            if (e.key === "v8-admin-debug-mode") {
                setDebugMode(e.newValue === "true");
            }
        };

        const handleCustomEvent = (e: Event) => {
            const customEvent = e as CustomEvent;
            setDebugMode(!!customEvent.detail);
        };

        window.addEventListener("storage", handleStorage);
        window.addEventListener("v8-debug-mode-change", handleCustomEvent as EventListener);
        
        return () => {
            window.removeEventListener("storage", handleStorage);
            window.removeEventListener("v8-debug-mode-change", handleCustomEvent as EventListener);
        };
    }, []);

    const toggleDebugMode = (enabled: boolean) => {
        if (typeof window === "undefined") {
            return;
        }
        localStorage.setItem("v8-admin-debug-mode", String(enabled));
        window.dispatchEvent(new CustomEvent("v8-debug-mode-change", { detail: enabled }));
        setDebugMode(enabled);
    };

    return [debugMode, toggleDebugMode] as const;
}
