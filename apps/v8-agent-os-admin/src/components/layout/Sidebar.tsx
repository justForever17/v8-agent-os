"use client";

import { useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { LogOut, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { signOut } from "next-auth/react";

import { ADMIN_NAV_GROUPS } from "@/lib/admin-navigation";
import { lt } from "@/lib/locale";
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

    return (
        <aside className="hidden h-screen w-80 shrink-0 overflow-hidden border-r border-slate-200 bg-[#f7fafc] lg:flex lg:flex-col">
            <div className="border-b border-slate-200 px-6 py-5">
                <div className="flex items-center gap-3">
                    <div className="relative h-11 w-11 overflow-hidden rounded-2xl bg-white shadow-sm ring-1 ring-slate-200">
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
                    <div>
                        <div className="text-base font-semibold text-slate-900 notranslate" translate="no">
                            V8 Agent OS
                        </div>
                        <div className="text-xs text-slate-500">{t(lt("查看状态与调整设置", "Observe and configure"))}</div>
                    </div>
                </div>
            </div>

            <div className="min-w-0 flex-1 overflow-y-auto">
                <div className="w-full min-w-0 space-y-6 px-4 py-5 pr-5">
                    {ADMIN_NAV_GROUPS.map((group) => {
                        const active = isGroupActive(pathname, group.items.map((item) => item.href));
                        const open = openGroups[group.id] ?? active;
                        return (
                            <section key={group.id} className="w-full min-w-0 space-y-2">
                                <button
                                    type="button"
                                    className="flex w-full min-w-0 items-center justify-between overflow-hidden rounded-2xl px-3 py-2 text-left transition-colors hover:bg-white/70"
                                    onClick={() => setOpenGroups((current) => ({ ...current, [group.id]: !open }))}
                                >
                                    <span className="truncate text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">{t(group.title)}</span>
                                    {open ? <PanelLeftClose className="h-4 w-4 text-slate-400" /> : <PanelLeftOpen className="h-4 w-4 text-slate-400" />}
                                </button>
                                {open ? (
                                    <div className="w-full min-w-0 space-y-1">
                                        {group.items.map((item) => {
                                            const selected = pathname === item.href || (item.href !== "/admin" && pathname.startsWith(item.href));
                                            return (
                                                <Link key={item.href} href={item.href} className="block min-w-0">
                                                    <div
                                                        className={cn(
                                                            "flex w-full min-w-0 items-start gap-3 overflow-hidden rounded-2xl px-3 py-3 transition-colors",
                                                            selected
                                                                ? "bg-white text-slate-900 shadow-sm ring-1 ring-sky-100"
                                                                : "text-slate-600 hover:bg-white/80 hover:text-slate-900"
                                                        )}
                                                        >
                                                            <item.icon className={cn("mt-0.5 h-4 w-4 shrink-0", selected ? "text-sky-600" : "text-slate-400")} />
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

            <div className="border-t border-slate-200 px-4 py-4">
                <Button
                    variant="outline"
                    className="w-full justify-start rounded-2xl border-slate-200 bg-white text-slate-600 hover:text-rose-600"
                    onClick={() => signOut()}
                >
                    <LogOut className="mr-2 h-4 w-4" />
                    {t(lt("退出登录", "Sign out"))}
                </Button>
            </div>
        </aside>
    );
}
