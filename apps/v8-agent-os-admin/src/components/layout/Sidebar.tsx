"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChevronDown, Loader2, LogOut, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { signOut } from "next-auth/react";

import { ADMIN_TASK_NAV } from "@/lib/admin-navigation";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { useT } from "@/components/providers/LocaleProvider";
import { useToast } from "@/components/ui/use-toast";

import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";

export function Sidebar() {
    const pathname = usePathname() || "/admin";
    const t = useT();
    const { toast } = useToast();
    const [signingOut, setSigningOut] = useState(false);

    const [isCollapsed, setIsCollapsed] = useState(false);
    const [mobileOpen, setMobileOpen] = useState(false);
    useEffect(() => {
        setIsCollapsed(localStorage.getItem("v8-admin-sidebar-collapsed") === "true");
    }, []);

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

    const navigation = <nav data-v8-context-menu-ignore onContextMenu={(event) => event.preventDefault()} aria-label={t("admin.experience.navigation")} className="flex min-h-0 flex-1 flex-col gap-1 select-none overflow-y-auto px-3 py-4">
        {ADMIN_TASK_NAV.map(group => {
            const item = group.items[0];
            const active = group.items.some(entry => pathname === entry.href || (entry.href !== "/admin" && pathname.startsWith(entry.href)));
            const Icon = item.icon;
            const primary = <Link href={item.href} prefetch={false} onClick={() => setMobileOpen(false)} aria-current={active ? "page" : undefined} className={cn("flex min-h-[36px] min-w-0 flex-1 items-center gap-3 rounded-lg px-3 py-2 text-[14px] font-medium transition-colors", active ? "bg-accent text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground")}><Icon size={16} className="shrink-0"/><span className="truncate">{t(group.title)}</span></Link>;
            return group.items.length === 1 ? <div key={group.id}>{primary}</div> : <details key={group.id} open={active || undefined} className="group/nav">
                <summary className="flex list-none items-center rounded-lg [&::-webkit-details-marker]:hidden">{primary}<span className="flex h-9 w-8 shrink-0 cursor-pointer items-center justify-center text-muted-foreground"><ChevronDown size={14} className="group-open/nav:rotate-180"/></span></summary>
                <div className="ml-7 mt-1 space-y-1 border-l border-border pl-2">{group.items.slice(1).map(entry => <Link key={entry.href} href={entry.href} prefetch={false} onClick={() => setMobileOpen(false)} aria-current={pathname === entry.href ? "page" : undefined} className={cn("block rounded-md px-3 py-2 text-[12px] transition-colors hover:bg-muted", pathname === entry.href ? "text-primary" : "text-muted-foreground")}>{t(entry.title)}</Link>)}</div>
            </details>;
        })}
        <Link href="/admin/advanced-governance" prefetch={false} onClick={() => setMobileOpen(false)} className="mt-auto rounded-lg px-3 py-3 text-[12px] text-muted-foreground hover:bg-muted">{t("admin.experience.diagnostics")}</Link>
        <Button variant="ghost" className="justify-start" onClick={() => void handleSignOut()} disabled={signingOut}>{signingOut ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> : <LogOut className="mr-2 h-4 w-4"/>}{t("components.layout.Sidebar.k2ed944b1")}</Button>
    </nav>;
    return <>
        <aside className={cn("relative hidden h-full min-h-0 shrink-0 flex-col border-r border-border bg-background lg:flex", isCollapsed ? "w-10" : "w-[224px]")}>
            <button type="button" onClick={toggleCollapse} className="ml-auto flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring" aria-label={t(isCollapsed ? "components.layout.Sidebar.expandSidebar" : "components.layout.Sidebar.collapseSidebar")}>{isCollapsed ? <PanelLeftOpen size={16}/> : <PanelLeftClose size={16}/>}</button>
            {!isCollapsed ? navigation : null}
        </aside>
        <Dialog open={mobileOpen} onOpenChange={setMobileOpen}>
            <DialogTrigger asChild><Button variant="outline" size="icon" className="fixed bottom-20 left-3 z-40 shadow-sm lg:hidden" aria-label={t("admin.experience.navigation")}><PanelLeftOpen size={18}/></Button></DialogTrigger>
            <DialogContent className="left-0 top-0 flex h-dvh max-h-dvh w-[min(320px,90vw)] translate-x-0 translate-y-0 flex-col gap-0 rounded-none p-0">
                <DialogHeader className="px-5 py-4"><DialogTitle>{t("admin.experience.navigation")}</DialogTitle></DialogHeader>
                {navigation}
            </DialogContent>
        </Dialog>
    </>;
}
