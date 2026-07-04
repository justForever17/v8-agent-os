"use client";

import { Bot } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";

export function RPAAuthGate() {
    const t = useT();

    return (
        <div className="flex h-full flex-col items-center justify-center px-6 py-10">
            <div className="w-full max-w-sm rounded-[2rem] border border-border/70 bg-background/90 p-7 text-center shadow-[0_28px_80px_-42px_rgba(15,23,42,0.45)] backdrop-blur-sm">
                <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-primary/10">
                    <Bot className="h-7 w-7 text-primary" />
                </div>
                <div className="mt-5 space-y-2">
                    <h1 className="text-2xl font-semibold tracking-tight">{t(lt("本机连接未就绪", "Local connection is not ready"))}</h1>
                    <p className="text-sm leading-6 text-muted-foreground">
                        {t(lt("请先回到聊天页完成自动连接，再打开 RPA 自动化。", "Open Chat first to finish local auto-connect, then return to RPA automation."))}
                    </p>
                </div>
                <div className="mt-6 flex justify-center">
                    <Button asChild className="rounded-2xl">
                        <a href="/chat">{t(lt("回到聊天页", "Back to Chat"))}</a>
                    </Button>
                </div>
            </div>
        </div>
    );
}
