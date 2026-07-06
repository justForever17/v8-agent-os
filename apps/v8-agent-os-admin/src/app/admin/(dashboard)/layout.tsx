"use client";

import { AdminPasswordGate } from "@/components/admin/AdminPasswordGate";
import { Sidebar } from "@/components/layout/Sidebar";
import { SessionProvider } from "@/components/providers/SessionProvider";
import { Topbar } from "@/components/layout/Topbar";
import { Toaster } from "@/components/ui/toaster";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
    return (
        <SessionProvider>
            <div className="flex h-screen flex-col overflow-hidden bg-[#f3f6fb] text-slate-950 [--primary-foreground:210_40%_98%] [--primary:262.1_83.3%_57.8%] [--ring:262.1_83.3%_57.8%] dark:bg-zinc-950 dark:text-zinc-50 dark:[--primary-foreground:210_40%_98%] dark:[--primary:263.4_70%_50.4%] dark:[--ring:263.4_70%_50.4%]">
                <Topbar />

                <div className="flex min-h-0 flex-1 overflow-hidden">
                    <Sidebar />
                    <main className="flex min-h-0 flex-1 flex-col overflow-hidden">
                        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
                            <div className="px-6 py-6 lg:px-8">
                                {children}
                            </div>
                        </div>
                    </main>
                </div>
                <AdminPasswordGate />
                <Toaster />
            </div>
        </SessionProvider>
    );
}
