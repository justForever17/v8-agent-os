"use client";

import { ProductTrafficLightWindowControls } from "@v8/product-ui";
import { useT } from "@/components/providers/LocaleProvider";

type ShellWindowApi = {
    isShell: true;
    minimize: () => void;
    toggleMaximize: () => void;
    close: () => void;
    openWeb: () => void;
    openAdmin: () => void;
};

declare global {
    interface Window {
        v8osShell?: ShellWindowApi;
    }
}

export function ShellWindowControls() {
    const t = useT();
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
    />;
}
