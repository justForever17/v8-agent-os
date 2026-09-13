"use client";

import { createContext, useContext, type ReactNode } from "react";
import { createPortal } from "react-dom";

/** Render the existing transaction's actions in the shell's non-scrolling row. */
export const AdminSaveHostContext = createContext<HTMLElement | null>(null);

export function AdminSaveBar({ children, error }: { children: ReactNode; error?: string }) {
    const host = useContext(AdminSaveHostContext);
    const actions = <div className="admin-save-bar flex flex-wrap items-center justify-end gap-3">{error ? <span role="alert" className="mr-auto max-w-full break-words text-sm text-destructive">{error}</span> : null}{children}</div>;
    return host ? createPortal(actions, host) : actions;
}
