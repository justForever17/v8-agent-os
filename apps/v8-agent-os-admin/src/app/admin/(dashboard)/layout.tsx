"use client";

import { AdminPasswordGate } from "@/components/admin/AdminPasswordGate";
import { Sidebar } from "@/components/layout/Sidebar";
import { SessionProvider } from "@/components/providers/SessionProvider";
import { Topbar } from "@/components/layout/Topbar";
import { Toaster } from "@/components/ui/toaster";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
    return (
        <SessionProvider>
            <div className="flex h-screen overflow-hidden bg-[#f3f6fb]">
                <Sidebar />

                <main className="flex min-h-0 flex-1 flex-col overflow-hidden">
                    <Topbar />
                    <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
                        <div className="px-6 py-6 lg:px-8">
                            {children}
                        </div>
                    </div>
                </main>
                <AdminPasswordGate />
                <Toaster />
            </div>
        </SessionProvider>
    );
}
