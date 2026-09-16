"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { flushAllDrafts } from "@/lib/composer-drafts";
import { setShellSurfaceVisible } from "@/hooks/use-surface-visible";

export function ShellLifecycle({ children }: { children: React.ReactNode }) {
    const router = useRouter();
    useEffect(() => {
        const shell = window.v8osShell;
        if (!shell) return;
        const offVisibility = shell.onSurfaceVisibilityChange?.(({ visible }) => {
            setShellSurfaceVisible(visible);
            if (!visible) void flushAllDrafts();
        });
        const offNavigation = shell.onNavigateSession?.(({ sessionId }) => {
            if (/^[A-Za-z0-9][A-Za-z0-9_.:-]{5,180}$/.test(sessionId)) router.push(`/chat?id=${encodeURIComponent(sessionId)}`);
        });
        return () => { offVisibility?.(); offNavigation?.(); };
    }, [router]);
    return children;
}
