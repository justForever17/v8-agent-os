"use client";

import { useState } from "react";
import { AdminSaveHostContext } from "@/components/admin-shell/AdminSaveBar";

import { AdminPasswordGate } from "@/components/admin/AdminPasswordGate";
import { Sidebar } from "@/components/layout/Sidebar";
import { SessionProvider } from "@/components/providers/SessionProvider";
import { Topbar } from "@/components/layout/Topbar";
import { Toaster } from "@/components/ui/toaster";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
    const [saveHost, setSaveHost] = useState<HTMLDivElement | null>(null);
    return (
        <SessionProvider>
            <AdminSaveHostContext.Provider value={saveHost}>
            <div className="admin-app flex h-dvh flex-col overflow-hidden bg-background text-foreground">
                <Topbar />

                <div className="flex min-h-0 flex-1 overflow-hidden">
                    <Sidebar />
                    <main className="flex min-h-0 flex-1 flex-col overflow-hidden">
                        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
                            <div className="admin-content px-4 py-5 lg:px-6">
                                {children}
                            </div>
                        </div>
                        <div ref={setSaveHost} id="admin-save-actions" className="shrink-0 empty:hidden" aria-live="polite" />
                    </main>
                </div>
                <AdminPasswordGate />
                <Toaster />
            </div>
            </AdminSaveHostContext.Provider>
        </SessionProvider>
    );
}
