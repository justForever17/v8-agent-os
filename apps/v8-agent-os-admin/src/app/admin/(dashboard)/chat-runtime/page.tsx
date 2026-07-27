"use client";

import { useEffect, useState } from "react";
import { Crown, Bot } from "lucide-react";

import { useT } from "@/components/providers/LocaleProvider";
import SupervisorPage from "../supervisor/page";
import SubagentsPage from "../subagents/page";
import { prefetchAdminRouteData } from "@/lib/admin-client-cache";
import { cn } from "@/lib/utils";

export default function ChatRuntimePage() {
    const t = useT();
    const [currentTab, setCurrentTab] = useState<"supervisor" | "subagents">("supervisor");
    const [isVisible, setIsVisible] = useState(true);

    useEffect(() => {
        let lastScrollTop = 0;
        let ticking = false;

        const handleScroll = (event: Event) => {
            const target = event.target as HTMLElement;
            // 确保目标滚动容器存在，并且有纵向滚动属性
            if (!target || target.scrollHeight === undefined) return;

            const scrollTop = target.scrollTop || 0;

            if (!ticking) {
                window.requestAnimationFrame(() => {
                    // 当页面处于最顶端附近时，必须保持显现状态
                    if (scrollTop < 15) {
                        setIsVisible(true);
                    } else if (scrollTop > lastScrollTop) {
                        // 向下滑动，隐藏切换条
                        setIsVisible(false);
                    } else {
                        // 向上滑动，呈现切换条
                        setIsVisible(true);
                    }
                    lastScrollTop = scrollTop;
                    ticking = false;
                });
                ticking = true;
            }
        };

        // 捕获阶段（useCapture = true）侦听容器的局部滚动
        window.addEventListener("scroll", handleScroll, true);
        return () => {
            window.removeEventListener("scroll", handleScroll, true);
        };
    }, []);

    return (
        <div className="relative min-h-full">
            {/* 子配置页面面板容器 */}
            <div className="pb-16">
                {currentTab === "supervisor" ? <SupervisorPage /> : <SubagentsPage />}
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
                            left: currentTab === "supervisor" ? "4px" : "calc(50% + 2px)",
                            width: "calc(50% - 6px)",
                        }}
                    />

                    <button
                        type="button"
                        onClick={() => setCurrentTab("supervisor")}
                        className={cn(
                            "relative z-10 px-5 py-2 rounded-full text-xs font-semibold flex items-center gap-1.5 transition-colors duration-300",
                            currentTab === "supervisor"
                                ? "text-white dark:text-slate-950"
                                : "text-muted-foreground hover:text-foreground dark:text-muted-foreground/80 dark:hover:text-slate-200"
                        )}
                    >
                        <Crown className="h-3.5 w-3.5" />
                        {t("app.admin.dashboard.chat.runtime.page.kbae659f3")}
                    </button>
                    <button
                        type="button"
                        onPointerEnter={() => void prefetchAdminRouteData("/admin/subagents")}
                        onFocus={() => void prefetchAdminRouteData("/admin/subagents")}
                        onClick={() => {
                            void prefetchAdminRouteData("/admin/subagents");
                            setCurrentTab("subagents");
                        }}
                        className={cn(
                            "relative z-10 px-5 py-2 rounded-full text-xs font-semibold flex items-center gap-1.5 transition-colors duration-300",
                            currentTab === "subagents"
                                ? "text-white dark:text-slate-950"
                                : "text-muted-foreground hover:text-foreground dark:text-muted-foreground/80 dark:hover:text-slate-200"
                        )}
                    >
                        <Bot className="h-3.5 w-3.5" />
                        {t("app.admin.dashboard.chat.runtime.page.k0354845a")}
                    </button>
                </div>
            </div>
        </div>
    );
}
