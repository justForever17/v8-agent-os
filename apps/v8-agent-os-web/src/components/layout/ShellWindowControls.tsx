"use client";

import { useEffect, useState } from "react";
import { ProductTrafficLightWindowControls } from "@v8/product-ui";
import { useT } from "@/components/providers/LocaleProvider";

type ShellWindowApi = {
    isShell: true;
    minimize: () => void;
    toggleMaximize: () => void;
    getWindowState: () => Promise<{ isMaximized?: boolean }>;
    onWindowStateChange: (callback: (state: { isMaximized?: boolean }) => void) => () => void;
    close: () => void;
    openWeb: () => void;
    openAdmin: () => void;
    reportActiveSession: (sessionId: string | null) => void;
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
