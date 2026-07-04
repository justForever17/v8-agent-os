"use client";

import { LocaleToggle } from "@/components/layout/LocaleToggle";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { UserProfile } from "@/components/layout/UserProfile";
import { VoiceToggle } from "@/components/layout/VoiceToggle";
import Image from "next/image";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";
import { Workflow } from "lucide-react";

export function Topbar() {
    const t = useT();

    return (
        <header className="sticky top-0 z-50 flex h-14 items-center justify-between border-b bg-background/95 px-3 backdrop-blur supports-[backdrop-filter]:bg-background/60 sm:px-4">
            <div className="flex min-w-0 items-center gap-2">
                <div className="relative h-8 w-8 overflow-hidden rounded-lg ring-1 ring-border/60">
                    <Image
                        src="/brand-mark.png"
                        alt={t(lt("V8 代理操作系统", "V8 Agent OS"))}
                        fill
                        sizes="32px"
                        className="object-cover notranslate"
                        priority
                        translate="no"
                    />
                </div>
                <h1 className="v8os-wordmark notranslate" aria-label={t(lt("V8 代理操作系统", "V8 Agent OS"))} translate="no">
                    <span className="v8os-wordmark__glow" aria-hidden="true">V8 Agent OS</span>
                    <span className="v8os-wordmark__shine" aria-hidden="true">V8 Agent OS</span>
                    <span className="v8os-wordmark__text">V8 Agent OS</span>
                </h1>
            </div>

            <div className="flex items-center gap-2 sm:gap-4">
                <Link href="/rpa">
                    <Button variant="ghost" size="sm" className="gap-2 px-2 sm:px-3">
                        <Workflow className="h-4 w-4" />
                        <span className="hidden sm:inline">{t(lt("RPA 自动化", "RPA automation"))}</span>
                    </Button>
                </Link>
                <LocaleToggle />
                <VoiceToggle />
                <ThemeToggle />
                <UserProfile />
            </div>
        </header>
    );
}
