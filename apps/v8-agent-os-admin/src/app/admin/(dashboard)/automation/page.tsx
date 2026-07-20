"use client";

import { useEffect, useState, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Cable, Clock3 } from "lucide-react";

import { useT } from "@/components/providers/LocaleProvider";
import HooksPage from "./hooks/page";
import ScheduledTasksPage from "./cron/page";
import { WakeIngressPolicyCard } from "@/components/automation/WakeIngressPolicyCard";
import { cn } from "@/lib/utils";

function AutomationOverviewInner() {
    const t = useT();
    const searchParams = useSearchParams();
    const router = useRouter();
    const tabParam = searchParams.get("tab");
    const currentTab: "hooks" | "cron" = tabParam === "cron" ? "cron" : "hooks";
    const [isVisible, setIsVisible] = useState(true);
    const selectTab = (tab: "hooks" | "cron") => {
        router.replace(`/admin/automation?tab=${tab}`, { scroll: false });
    };

    // 复制与 chat-runtime 一致的滚动监听逻辑以控制浮动切换条显隐
    useEffect(() => {
        let lastScrollTop = 0;
        let ticking = false;

        const handleScroll = (event: Event) => {
            const target = event.target as HTMLElement;
            if (!target || target.scrollHeight === undefined) return;

            const scrollTop = target.scrollTop || 0;

            if (!ticking) {
                window.requestAnimationFrame(() => {
                    if (scrollTop < 15) {
                        setIsVisible(true);
                    } else if (scrollTop > lastScrollTop) {
                        // 向下滑动，隐藏切换条
                        setIsVisible(false);
                    } else {
                        // 向上滑动，显示切换条
                        setIsVisible(true);
                    }
                    lastScrollTop = scrollTop;
                    ticking = false;
                });
                ticking = true;
            }
        };

        window.addEventListener("scroll", handleScroll, true);
        return () => {
            window.removeEventListener("scroll", handleScroll, true);
        };
    }, []);

    return (
        <div className="relative min-h-full">
            {/* 配置页面面板容器 */}
            <div className="pb-16">
                {currentTab === "hooks" ? (
                    <div className="space-y-8">
                        <HooksPage />
                        <div className="w-full">
                            <WakeIngressPolicyCard />
                        </div>
                    </div>
                ) : (
                    <ScheduledTasksPage />
                )}
            </div>

            {/* 居中毛玻璃段式胶囊切换控制条 */}
            <div
                className={cn(
                    "fixed bottom-6 left-1/2 -translate-x-1/2 z-50 transition-all duration-300 ease-in-out",
                    isVisible
                        ? "translate-y-0 opacity-100 scale-100"
                        : "translate-y-20 opacity-0 scale-95 pointer-events-none"
                )}
            >
                <div className="relative flex items-center bg-card/75 dark:bg-slate-900/75 backdrop-blur-md p-1 rounded-full border border-border/80 dark:border-slate-800/80 shadow-[0_8px_30px_rgba(0,0,0,0.12)]">
                    {/* 滑块平移动画背景 */}
                    <div
                        className="absolute top-1 bottom-1 rounded-full bg-slate-950 dark:bg-muted shadow-sm transition-all duration-300 ease-out"
                        style={{
                            left: currentTab === "hooks" ? "4px" : "calc(50% + 2px)",
                            width: "calc(50% - 6px)",
                        }}
                    />

                    <button
                        type="button"
                        onClick={() => selectTab("hooks")}
                        className={cn(
                            "relative z-10 px-5 py-2 rounded-full text-xs font-semibold flex items-center gap-1.5 transition-colors duration-300",
                            currentTab === "hooks"
                                ? "text-white dark:text-slate-950"
                                : "text-muted-foreground hover:text-foreground dark:text-muted-foreground/80 dark:hover:text-slate-200"
                        )}
                    >
                        <Cable className="h-3.5 w-3.5" />
                        {t("app.admin.dashboard.automation.page.ka206d935")}
                    </button>
                    <button
                        type="button"
                        onClick={() => selectTab("cron")}
                        className={cn(
                            "relative z-10 px-5 py-2 rounded-full text-xs font-semibold flex items-center gap-1.5 transition-colors duration-300",
                            currentTab === "cron"
                                ? "text-white dark:text-slate-950"
                                : "text-muted-foreground hover:text-foreground dark:text-muted-foreground/80 dark:hover:text-slate-200"
                        )}
                    >
                        <Clock3 className="h-3.5 w-3.5" />
                        {t("app.admin.dashboard.automation.page.k8164146c")}
                    </button>
                </div>
            </div>
        </div>
    );
}

export default function AutomationOverviewPage() {
    return (
        <Suspense fallback={<div className="flex h-48 items-center justify-center text-sm text-muted-foreground">Loading Automation Runtime...</div>}>
            <AutomationOverviewInner />
        </Suspense>
    );
}
