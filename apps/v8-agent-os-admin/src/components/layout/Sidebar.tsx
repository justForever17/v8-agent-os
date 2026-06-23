"use client";

import { useState, useEffect } from "react";
import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { LogOut, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { signOut } from "next-auth/react";

import { ADMIN_NAV_GROUPS } from "@/lib/admin-navigation";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { useT } from "@/components/providers/LocaleProvider";

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
    const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});
    const t = useT();

    // 1. 初始化并从 localStorage 恢复折叠状态
    const [isCollapsed, setIsCollapsed] = useState(false);
    useEffect(() => {
        const stored = localStorage.getItem("v8-admin-sidebar-collapsed");
        if (stored === "true") {
            setIsCollapsed(true);
        }
    }, []);

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
                "hidden h-full min-h-0 shrink-0 overflow-hidden border-r border-slate-200 bg-[#f7fafc] lg:flex lg:flex-col transition-all duration-300 ease-in-out",
                isCollapsed ? "w-[76px]" : "w-80"
            )}
        >
            {/* 头部 Logo 区域 */}
            <div className={cn("border-b border-slate-200 px-4 py-5 flex items-center", isCollapsed ? "justify-center" : "px-6")}>
                <div className="flex items-center gap-3">
                    <div className="relative h-11 w-11 shrink-0 overflow-hidden rounded-2xl bg-white shadow-sm ring-1 ring-slate-200">
                        <Image
                            src="/brand-mark.png"
                            alt="V8 Agent OS"
                            fill
                            sizes="44px"
                            className="object-cover notranslate"
                            priority
                            translate="no"
                        />
                    </div>
                    {!isCollapsed && (
                        <div className="transition-opacity duration-300">
                            <h1 className="v8os-wordmark notranslate" aria-label="V8 Agent OS" translate="no">
                                <span className="v8os-wordmark__glow" aria-hidden="true">V8 Agent OS</span>
                                <span className="v8os-wordmark__shine" aria-hidden="true">V8 Agent OS</span>
                                <span className="v8os-wordmark__text">V8 Agent OS</span>
                            </h1>
                            <div className="text-xs text-slate-500 mt-0.5">{t("components.layout.Sidebar.k8021804f")}</div>
                        </div>
                    )}
                </div>
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
                                        className="flex w-full min-w-0 items-center justify-between overflow-hidden rounded-2xl px-3 py-2 text-left transition-colors hover:bg-white/70"
                                        onClick={() => setOpenGroups((current) => ({ ...current, [group.id]: !open }))}
                                    >
                                        <span className="truncate text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">{t(group.title)}</span>
                                        {open ? <PanelLeftClose className="h-4 w-4 text-slate-400" /> : <PanelLeftOpen className="h-4 w-4 text-slate-400" />}
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
                                                                ? "bg-white text-slate-900 shadow-sm ring-1 ring-sky-100"
                                                                : "text-slate-600 hover:bg-white/80 hover:text-slate-900"
                                                        )}
                                                    >
                                                        {/* 图标与微型徽标 */}
                                                        <div className="relative flex items-center justify-center shrink-0">
                                                            <item.icon className={cn("h-4 w-4", isCollapsed ? "" : "mt-0.5", selected ? "text-sky-600" : "text-slate-400")} />
                                                            {isCollapsed && item.badge ? (
                                                                <span 
                                                                    className={cn(
                                                                        "absolute -top-1 -right-1 h-2 w-2 rounded-full ring-2 ring-[#f7fafc]", 
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
                                                                <div className="min-w-0 truncate text-xs leading-5 text-slate-500">
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
            <div className="border-t border-slate-200 p-4 space-y-3 shrink-0">
                {/* 退出登录按钮 */}
                <Button
                    variant="outline"
                    className={cn(
                        "border-slate-200 bg-white text-slate-600 hover:text-rose-600 transition-all duration-300",
                        isCollapsed ? "h-11 w-11 p-0 rounded-2xl mx-auto flex items-center justify-center" : "w-full justify-start rounded-2xl"
                    )}
                    onClick={() => signOut()}
                    title={isCollapsed ? t("components.layout.Sidebar.k2ed944b1") : undefined}
                >
                    <LogOut className={cn("h-4 w-4", isCollapsed ? "" : "mr-2")} />
                    {!isCollapsed && t("components.layout.Sidebar.k2ed944b1")}
                </Button>

                {/* 展开/收起控制条 */}
                <div className={cn("flex items-center", isCollapsed ? "justify-center" : "justify-between px-2 pt-1")}>
                    <button
                        type="button"
                        onClick={toggleCollapse}
                        className="rounded-xl p-1.5 text-slate-400 hover:bg-slate-200/50 hover:text-slate-600 transition-colors"
                        title={isCollapsed ? "展开侧边栏" : "折叠侧边栏"}
                    >
                        {isCollapsed ? <PanelLeftOpen className="h-4.5 w-4.5" /> : <PanelLeftClose className="h-4.5 w-4.5" />}
                    </button>
                    {!isCollapsed && (
                        <span className="text-[10px] text-slate-400 uppercase font-medium tracking-wider notranslate" translate="no">
                            V8 Agent OS
                        </span>
                    )}
                </div>
            </div>
        </aside>
    );
}
