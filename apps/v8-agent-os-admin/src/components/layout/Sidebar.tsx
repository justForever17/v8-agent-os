"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Loader2, LogOut, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { signOut } from "next-auth/react";

import { ADMIN_NAV_GROUPS } from "@/lib/admin-navigation";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { useT } from "@/components/providers/LocaleProvider";
import { useToast } from "@/components/ui/use-toast";

function badgeClasses(tone: "beta" | "dev") {
    if (tone === "dev") {
        return "border-sky-200 bg-sky-50 text-sky-700";
    }
    return "border-emerald-200 bg-emerald-50 text-emerald-700";
}

export function Sidebar() {
    const pathname = usePathname() || "/admin";
    const t = useT();
    const { toast } = useToast();
    const [signingOut, setSigningOut] = useState(false);

    const [isCollapsed, setIsCollapsed] = useState(() => {
        if (typeof window === "undefined") {
            return false;
        }
        return localStorage.getItem("v8-admin-sidebar-collapsed") === "true";
    });

    const toggleCollapse = () => {
        setIsCollapsed((prev) => {
            const next = !prev;
            localStorage.setItem("v8-admin-sidebar-collapsed", String(next));
            return next;
        });
    };

    const handleSignOut = async () => {
        if (signingOut) return;
        setSigningOut(true);
        let shellLocked = false;
        try {
            const localLoginUrl = new URL("/login", window.location.origin).toString();
            let canonicalLoginUrl = localLoginUrl;
            if (window.v8osShell?.isShell && window.v8osShell.lockAdminSession) {
                const lock = await window.v8osShell.lockAdminSession();
                if (!lock?.locked || !lock.loginUrl) throw new Error("shell_admin_lock_failed");
                shellLocked = true;
                canonicalLoginUrl = lock.loginUrl;
            }
            await signOut({ redirect: false, redirectTo: "/login" });
            const sessionResponse = await fetch("/api/auth/session", {
                cache: "no-store",
                credentials: "same-origin",
            });
            if (!sessionResponse.ok) throw new Error("admin_session_probe_failed");
            const remainingSession = await sessionResponse.json().catch(() => null);
            if (remainingSession?.user) throw new Error("admin_session_not_cleared");
            window.location.replace(canonicalLoginUrl);
        } catch (error) {
            if (shellLocked) {
                console.error("[v8os-admin] sign-out failed; reloading to reconcile the Admin session", error);
                window.location.reload();
                return;
            }
            setSigningOut(false);
            toast({
                title: t("components.layout.Sidebar.signOutFailed"),
                variant: "destructive",
            });
        }
    };

    return (
        <aside 
            className={cn(
                "relative hidden h-full min-h-0 shrink-0 overflow-visible bg-[#f7fafc] transition-[width] [transition-duration:220ms] [transition-timing-function:var(--v8-product-motion)] motion-reduce:[transition-duration:150ms] dark:bg-zinc-950 lg:flex lg:flex-col",
                isCollapsed ? "w-0" : "w-80 border-r border-border dark:border-white/10"
            )}
        >
            <button
                type="button"
                onClick={toggleCollapse}
                className={cn(
                    "absolute top-8 z-40 flex h-7 w-7 items-center justify-center text-muted-foreground opacity-70 transition-[left,right,color,opacity] hover:text-foreground hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60",
                    isCollapsed ? "left-3" : "-right-3.5",
                )}
                title={t(isCollapsed ? "components.layout.Sidebar.expandSidebar" : "components.layout.Sidebar.collapseSidebar")}
                aria-label={t(isCollapsed ? "components.layout.Sidebar.expandSidebar" : "components.layout.Sidebar.collapseSidebar")}
            >
                {isCollapsed ? <PanelLeftOpen className="h-3.5 w-3.5" /> : <PanelLeftClose className="h-3.5 w-3.5" />}
            </button>

            <div className={cn(
                "flex h-full w-80 min-w-0 flex-col transition-[opacity,transform] [transition-duration:180ms] [transition-timing-function:var(--v8-product-motion)] motion-reduce:transform-none motion-reduce:[transition-duration:150ms]",
                isCollapsed ? "pointer-events-none -translate-x-1.5 opacity-0" : "translate-x-0 opacity-100",
            )} aria-hidden={isCollapsed} inert={isCollapsed}>
            {/* 导航菜单区域 */}
            <div
                data-v8-context-menu-ignore
                className="min-w-0 flex-1 select-none overflow-y-auto overscroll-contain"
                onContextMenu={(event) => event.preventDefault()}
            >
                <div className="w-full min-w-0 space-y-6 px-4 py-5 pr-5">
                    {ADMIN_NAV_GROUPS.map((group) => {
                        return (
                            <section key={group.id} className="w-full min-w-0 space-y-2">
                                {(
                                    <div className="w-full min-w-0 overflow-hidden px-3 py-2 text-left">
                                        <span className="truncate text-xs font-semibold uppercase tracking-[0.22em] text-muted-foreground">{t(group.title)}</span>
                                    </div>
                                )}
                                <div className="w-full min-w-0 space-y-1">
                                    {group.items.map((item) => {
                                        const selected = pathname === item.href || (item.href !== "/admin" && pathname.startsWith(item.href));
                                        return (
                                            <Link
                                                key={item.href}
                                                href={item.href}
                                                prefetch={false}
                                                className="block min-w-0"
                                            >
                                                <div
                                                    className={cn(
                                                        "flex min-w-0 items-center transition-all duration-200",
                                                        "w-full items-start gap-3 rounded-2xl px-3 py-3",
                                                        selected
                                                            ? "bg-card text-foreground shadow-sm ring-1 ring-sky-100 dark:bg-white/[0.08] dark:text-slate-100 dark:ring-sky-500/20"
                                                            : "text-muted-foreground hover:bg-card/80 hover:text-foreground dark:text-slate-300 dark:hover:bg-white/[0.06] dark:hover:text-slate-100"
                                                    )}
                                                >
                                                    {/* 图标与微型徽标 */}
                                                    <div className="relative flex items-center justify-center shrink-0">
                                                        <item.icon className={cn("h-4 w-4 mt-0.5", selected ? "text-sky-600 dark:text-sky-300" : "text-muted-foreground")} />
                                                    </div>

                                                    {/* 文本描述：折叠时隐藏 */}
                                                    {(
                                                        <div className="min-w-0 flex-1 space-y-1">
                                                            <div className="flex min-w-0 items-center gap-2 overflow-hidden">
                                                                <div className="min-w-0 flex-1 truncate text-sm font-medium">
                                                                    {t(item.title)}
                                                                </div>
                                                                {item.badge ? (
                                                                    <span className={cn("shrink-0 rounded-full border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide", badgeClasses(item.badge.tone))}>
                                                                        {t(item.badge.label)}
                                                                    </span>
                                                                ) : null}
                                                            </div>
                                                            <div className="min-w-0 truncate text-xs leading-5 text-muted-foreground dark:text-muted-foreground">
                                                                {t(item.description)}
                                                            </div>
                                                        </div>
                                                    )}
                                                </div>
                                            </Link>
                                        );
                                    })}
                                </div>
                            </section>
                        );
                    })}
                </div>
            </div>

            {/* 底部控制面板 */}
            <div className="shrink-0 border-t border-border p-4 dark:border-white/10">
                    <div className="flex items-center">
                        <Button
                            variant="outline"
                            className="h-11 flex-1 justify-start rounded-2xl border-border bg-card text-muted-foreground transition-all duration-300 hover:text-rose-600 dark:border-white/10 dark:bg-white/[0.06] dark:text-slate-300 dark:hover:bg-white/[0.1] dark:hover:text-rose-300"
                            onClick={() => void handleSignOut()}
                            disabled={signingOut}
                        >
                            {signingOut ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <LogOut className="mr-2 h-4 w-4" />}
                            {t("components.layout.Sidebar.k2ed944b1")}
                        </Button>
                    </div>
            </div>
            </div>
        </aside>
    );
}
