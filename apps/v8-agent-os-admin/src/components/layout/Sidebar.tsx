"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ArrowLeft, LogOut, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { signOut } from "next-auth/react";

import { ADMIN_NAV_GROUPS } from "@/lib/admin-navigation";
import { prefetchAdminRouteData } from "@/lib/admin-client-cache";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { useT } from "@/components/providers/LocaleProvider";

const WEB_CHAT_URL = "http://localhost:9527/chat";

function isGroupActive(pathname: string, hrefs: string[]) {
    return hrefs.some((href) => pathname === href || (href !== "/admin" && pathname.startsWith(href)));
}

function badgeClasses(tone: "beta" | "dev") {
    if (tone === "dev") {
        return "border-sky-200 bg-sky-50 text-sky-700";
    }
    return "border-emerald-200 bg-emerald-50 text-emerald-700";
}

export function Sidebar() {
    const pathname = usePathname() || "/admin";
    const router = useRouter();
    const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});
    const t = useT();

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

    return (
        <aside 
            className={cn(
                "hidden h-full min-h-0 shrink-0 overflow-hidden border-r border-border bg-[#f7fafc] transition-all duration-300 ease-in-out dark:border-white/10 dark:bg-zinc-950 lg:flex lg:flex-col",
                isCollapsed ? "w-[76px]" : "w-80"
            )}
        >

            <div className={cn(
                "flex h-9 shrink-0 items-center border-b border-border dark:border-white/10",
                isCollapsed ? "justify-center px-0" : "px-3"
            )}>
                <a
                    href={WEB_CHAT_URL}
                    className={cn(
                        "flex h-7 w-7 items-center justify-center rounded-xl text-muted-foreground transition-colors hover:bg-card hover:text-foreground dark:text-muted-foreground dark:hover:bg-white/[0.06] dark:hover:text-slate-100",
                    )}
                    title={t("components.layout.Sidebar.backToChat")}
                >
                    <ArrowLeft className="h-4 w-4" />
                </a>
            </div>

            {/* 导航菜单区域 */}
            <div className="min-w-0 flex-1 overflow-y-auto overscroll-contain">
                <div className={cn("w-full min-w-0 space-y-6 px-4 py-5", isCollapsed ? "px-2" : "pr-5")}>
                    {ADMIN_NAV_GROUPS.map((group) => {
                        const active = isGroupActive(pathname, group.items.map((item) => item.href));
                        // 折叠状态下，强制展开所有组显示出图标
                        const open = isCollapsed ? true : (openGroups[group.id] ?? active);
                        
                        return (
                            <section key={group.id} className="w-full min-w-0 space-y-2">
                                {/* 分组标题：折叠时隐藏 */}
                                {!isCollapsed && (
                                    <button
                                        type="button"
                                        className="flex w-full min-w-0 items-center justify-between overflow-hidden rounded-2xl px-3 py-2 text-left transition-colors hover:bg-card/70 dark:hover:bg-white/[0.06]"
                                        onClick={() => setOpenGroups((current) => ({ ...current, [group.id]: !open }))}
                                    >
                                        <span className="truncate text-xs font-semibold uppercase tracking-[0.22em] text-muted-foreground">{t(group.title)}</span>
                                        {open ? <PanelLeftClose className="h-4 w-4 text-muted-foreground" /> : <PanelLeftOpen className="h-4 w-4 text-muted-foreground" />}
                                    </button>
                                )}
                                
                                {open ? (
                                    <div className={cn("w-full min-w-0 space-y-1", isCollapsed ? "flex flex-col items-center gap-1.5" : "")}>
                                        {group.items.map((item) => {
                                            const selected = pathname === item.href || (item.href !== "/admin" && pathname.startsWith(item.href));
                                            return (
                                                <Link 
                                                    key={item.href} 
                                                    href={item.href} 
                                                    prefetch={false}
                                                    onPointerEnter={() => {
                                                        router.prefetch(item.href);
                                                        void prefetchAdminRouteData(item.href);
                                                    }}
                                                    onFocus={() => {
                                                        router.prefetch(item.href);
                                                        void prefetchAdminRouteData(item.href);
                                                    }}
                                                    className="block min-w-0" 
                                                    title={isCollapsed ? t(item.title) : undefined}
                                                >
                                                    <div
                                                        className={cn(
                                                            "flex min-w-0 items-center transition-all duration-200",
                                                            isCollapsed 
                                                                ? "h-11 w-11 rounded-2xl justify-center" 
                                                                : "w-full items-start gap-3 rounded-2xl px-3 py-3",
                                                            selected
                                                                ? "bg-card text-foreground shadow-sm ring-1 ring-sky-100 dark:bg-white/[0.08] dark:text-slate-100 dark:ring-sky-500/20"
                                                                : "text-muted-foreground hover:bg-card/80 hover:text-foreground dark:text-slate-300 dark:hover:bg-white/[0.06] dark:hover:text-slate-100"
                                                        )}
                                                    >
                                                        {/* 图标与微型徽标 */}
                                                        <div className="relative flex items-center justify-center shrink-0">
                                                            <item.icon className={cn("h-4 w-4", isCollapsed ? "" : "mt-0.5", selected ? "text-sky-600 dark:text-sky-300" : "text-muted-foreground")} />
                                                            {isCollapsed && item.badge ? (
                                                                <span 
                                                                    className={cn(
                                                                        "absolute -top-1 -right-1 h-2 w-2 rounded-full ring-2 ring-[#f7fafc] dark:ring-zinc-950",
                                                                        item.badge.tone === "dev" ? "bg-sky-500" : "bg-emerald-500"
                                                                    )} 
                                                                />
                                                            ) : null}
                                                        </div>

                                                        {/* 文本描述：折叠时隐藏 */}
                                                        {!isCollapsed && (
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
                                ) : null}
                            </section>
                        );
                    })}
                </div>
            </div>

            {/* 底部控制面板 */}
            <div className={cn("shrink-0 border-t border-border dark:border-white/10", isCollapsed ? "p-2" : "p-4")}>
                {isCollapsed ? (
                    <button
                        type="button"
                        onClick={toggleCollapse}
                        className="mx-auto flex h-11 w-11 items-center justify-center rounded-2xl text-muted-foreground transition-colors hover:bg-card hover:text-muted-foreground dark:hover:bg-white/[0.06] dark:hover:text-slate-100"
                        title={t("components.layout.Sidebar.expandSidebar")}
                    >
                        <PanelLeftOpen className="h-4.5 w-4.5" />
                    </button>
                ) : (
                    <div className="flex items-center gap-2">
                        <Button
                            variant="outline"
                            className="h-11 flex-1 justify-start rounded-2xl border-border bg-card text-muted-foreground transition-all duration-300 hover:text-rose-600 dark:border-white/10 dark:bg-white/[0.06] dark:text-slate-300 dark:hover:bg-white/[0.1] dark:hover:text-rose-300"
                            onClick={() => signOut()}
                        >
                            <LogOut className="mr-2 h-4 w-4" />
                            {t("components.layout.Sidebar.k2ed944b1")}
                        </Button>
                        <button
                            type="button"
                            onClick={toggleCollapse}
                            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl text-muted-foreground transition-colors hover:bg-card hover:text-muted-foreground dark:hover:bg-white/[0.06] dark:hover:text-slate-100"
                            title={t("components.layout.Sidebar.collapseSidebar")}
                        >
                            <PanelLeftClose className="h-4.5 w-4.5" />
                        </button>
                    </div>
                )}
            </div>
        </aside>
    );
}
