"use client";

import { Bot } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useT } from "@/components/providers/LocaleProvider";

export function RPAAuthGate() {
    const t = useT();

    return (
        <div className="flex h-full flex-col items-center justify-center px-6 py-10">
            <div className="w-full max-w-sm rounded-[2rem] border border-border/70 bg-background/90 p-7 text-center shadow-[0_28px_80px_-42px_rgba(15,23,42,0.45)] backdrop-blur-sm">
                <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-primary/10">
                    <Bot className="h-7 w-7 text-primary" />
                </div>
                <div className="mt-5 space-y-2">
                    <h1 className="text-2xl font-semibold tracking-tight">{t("web.generated.bb5e360821")}</h1>
                    <p className="text-sm leading-6 text-muted-foreground">
                        {t("web.generated.e1c5b126e3")}
                    </p>
                </div>
                <div className="mt-6 flex justify-center">
                    <Button asChild className="rounded-2xl">
                        <a href="/chat">{t("web.generated.b15f0eec49")}</a>
                    </Button>
                </div>
            </div>
        </div>
    );
}
