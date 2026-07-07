"use client";

import { useEffect, useState } from "react";

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
    const [enabled, setEnabled] = useState(false);

    useEffect(() => {
        setEnabled(Boolean(window.v8osShell?.isShell));
    }, []);

    if (!enabled) {
        return null;
    }

    return (
        <div className="flex h-8 items-center overflow-hidden rounded-full border border-slate-200 bg-white/85 text-slate-500 shadow-sm backdrop-blur">
            <button
                type="button"
                aria-label="最小化"
                className="h-8 w-10 text-sm transition hover:bg-slate-100 hover:text-slate-900"
                onClick={() => window.v8osShell?.minimize()}
            >
                -
            </button>
            <button
                type="button"
                aria-label="最大化或还原"
                className="h-8 w-10 text-xs transition hover:bg-slate-100 hover:text-slate-900"
                onClick={() => window.v8osShell?.toggleMaximize()}
            >
                □
            </button>
            <button
                type="button"
                aria-label="隐藏到托盘"
                className="h-8 w-10 text-sm transition hover:bg-rose-50 hover:text-rose-600"
                onClick={() => window.v8osShell?.close()}
            >
                ×
            </button>
        </div>
    );
}
