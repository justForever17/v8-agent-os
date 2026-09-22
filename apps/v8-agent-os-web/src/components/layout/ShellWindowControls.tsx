"use client";

import { useEffect, useState } from "react";
import { ProductTrafficLightWindowControls } from "@v8/product-ui";
import { useT } from "@/components/providers/LocaleProvider";

export type ShellWindowApi = {
    isShell: true;
    onSurfaceVisibilityChange?: (callback: (state: { visible: boolean }) => void) => () => void;
    onNavigateSession?: (callback: (state: { sessionId: string }) => void) => () => void;
    minimize: () => void;
    toggleMaximize: () => void;
    getWindowState: () => Promise<{ isMaximized?: boolean }>;
    onWindowStateChange: (callback: (state: { isMaximized?: boolean }) => void) => () => void;
    close: () => void;
    openWeb: () => void;
    openAdmin: () => void;
    openWorkspaceFolder: (workspacePath: string) => Promise<{ ok?: boolean; error?: string }>;
    revealWorkspaceFile: (workspaceRelativePath: string, workspacePath: string) => Promise<{ ok?: boolean; error?: string }>;
    reportActiveSession: (sessionId: string | null) => void;
    getAdminSessionLock: () => Promise<{ locked: boolean; loginUrl: string }>;
    lockAdminSession: () => Promise<{ locked: boolean; loginUrl: string }>;
    onAdminSessionLockChange: (callback: (state: { locked: boolean; loginUrl: string }) => void) => () => void;
    getUpdateStatus?: () => Promise<any>;
    checkForUpdates?: () => Promise<any>;
    openUpdateRelease?: () => Promise<boolean>;
    selectGodotExecutable: () => Promise<{ ok: boolean; path?: string; cancelled?: boolean; error?: string }>;
    selectGodotProjectDirectory: () => Promise<{ ok: boolean; path?: string; cancelled?: boolean; error?: string }>;
    getDesktopPetState: () => Promise<any>;
    setDesktopPetEnabled: (enabled: boolean) => Promise<any>;
    onDesktopPetStateChange: (callback: (state: any) => void) => () => void;
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
        closeLabel={t("web.windowControls.close")}
        minimizeLabel={t("web.windowControls.minimize")}
        maximizeLabel={t("web.windowControls.maximize")}
        restoreLabel={t("web.windowControls.restore")}
        isMaximized={isMaximized}
    />;
}
