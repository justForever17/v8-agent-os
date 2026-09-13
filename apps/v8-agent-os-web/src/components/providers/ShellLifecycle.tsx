"use client";
import { useEffect, useState } from "react";
import { signOut, useSession } from "next-auth/react";
import { useRouter } from "next/navigation";
import { flushAllDrafts, removeDrafts } from "@/lib/composer-drafts";
import { setShellSurfaceVisible } from "@/hooks/use-surface-visible";

export function ShellLifecycle({ children }: { children: React.ReactNode }) {
    const router = useRouter();
    const { data: session } = useSession();
    const [locked, setLocked] = useState(false);
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
        const handleLock = ({ locked: next }: { locked: boolean }) => {
            setLocked(next);
            if (next) {
                const principal = session?.user?.id;
                if (principal) void removeDrafts((key) => { try { return JSON.parse(key)[1] === principal; } catch { return false; } });
                void signOut({ redirect: false });
            } else router.refresh();
        };
        const offLock = shell.onAdminSessionLockChange?.(handleLock);
        void shell.getAdminSessionLock?.().then((state) => { if (state.locked) handleLock(state); }).catch(() => undefined);
        return () => { offVisibility?.(); offNavigation?.(); offLock?.(); };
    }, [router, session?.user?.id]);
    return locked ? <div role="status" className="p-6">已锁定，请在管理台登录。</div> : children;
}
