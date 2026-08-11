"use client";

import { useEffect, useState } from "react";
import { ProductTrafficLightWindowControls } from "@v8/product-ui";
import { useT } from "@/components/providers/LocaleProvider";

export type ShellDesktopPetState = {
    state: "stopped" | "starting" | "waiting_v8os" | "connected" | "stopping" | "error";
    processRunning: boolean;
    controlConnected: boolean;
    activeSessionId?: string | null;
    enabled: boolean;
};

type ShellPickerResult = { ok: boolean; path?: string; cancelled?: boolean; error?: string };

export type ShellUpdateStatus = {
    state: "idle" | "disabled" | "checking" | "current" | "available" | "error";
    currentVersion?: string | null;
    version?: string | null;
    tag?: string | null;
    releaseUrl?: string | null;
    errorCode?: string | null;
};

type ShellWindowApi = {
    isShell: true;
    minimize: () => void;
    toggleMaximize: () => void;
    getWindowState: () => Promise<{ isMaximized?: boolean }>;
    onWindowStateChange: (callback: (state: { isMaximized?: boolean }) => void) => () => void;
    close: () => void;
    openWeb: () => void;
    openAdmin: () => void;
    getUpdateStatus?: () => Promise<ShellUpdateStatus>;
    checkForUpdates?: () => Promise<ShellUpdateStatus>;
    openUpdateRelease?: () => Promise<boolean>;
    selectGodotExecutable: () => Promise<ShellPickerResult>;
    selectGodotProjectDirectory: () => Promise<ShellPickerResult>;
    getDesktopPetState: () => Promise<ShellDesktopPetState>;
    setDesktopPetEnabled: (enabled: boolean) => Promise<ShellDesktopPetState>;
    onDesktopPetStateChange: (callback: (state: ShellDesktopPetState) => void) => () => void;
};

declare global {
    interface Window {
        v8osShell?: ShellWindowApi;
    }
}

export function ShellWindowControls() {
    const t = useT();
    const [isMaximized, setIsMaximized] = useState(false);

    useEffect(() => {
        const shell = window.v8osShell;
        if (!shell?.isShell) return;
        let mounted = true;
        void shell.getWindowState().then((state) => {
            if (mounted) setIsMaximized(Boolean(state?.isMaximized));
        });
        const unsubscribe = shell.onWindowStateChange((state) => {
            setIsMaximized(Boolean(state?.isMaximized));
        });
        return () => {
            mounted = false;
            unsubscribe();
        };
    }, []);

    if (typeof window === "undefined" || !window.v8osShell?.isShell) {
        return null;
    }

    return <ProductTrafficLightWindowControls
        onClose={() => window.v8osShell?.close()}
        onMinimize={() => window.v8osShell?.minimize()}
        onToggleMaximize={() => window.v8osShell?.toggleMaximize()}
        closeLabel={t("layout.windowControls.close")}
        minimizeLabel={t("layout.windowControls.minimize")}
        maximizeLabel={t("layout.windowControls.maximize")}
        restoreLabel={t("layout.windowControls.restore")}
        isMaximized={isMaximized}
    />;
}
