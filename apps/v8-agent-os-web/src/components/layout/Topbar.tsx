"use client";

import type { ReactNode } from "react";
import { LocaleToggle } from "@/components/layout/LocaleToggle";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { UserProfile } from "@/components/layout/UserProfile";
import { VoiceToggle } from "@/components/layout/VoiceToggle";
import Image from "next/image";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { useT } from "@/components/providers/LocaleProvider";
import { Workflow } from "lucide-react";

export function Topbar({ windowControls }: { windowControls?: ReactNode }) {
    const t = useT();

    return (
        <header className="sticky top-0 z-50 flex h-[35px] items-center justify-between border-b bg-background/95 px-3 backdrop-blur supports-[backdrop-filter]:bg-background/60 sm:px-4">
            <div className="flex h-[25px] min-w-0 items-center gap-2">
                <div className="relative h-[25px] w-[25px] overflow-hidden rounded-lg ring-1 ring-border/60">
                    <Image
                        src="/brand-mark.png"
                        alt={t("web.generated.e42cc67653")}
                        fill
                        sizes="25px"
                        className="object-cover notranslate"
                        priority
                        translate="no"
                    />
                </div>
                <h1 className="v8os-wordmark notranslate [&>span]:!min-w-[7.4rem] [&>span]:!pb-0 [&>span]:!text-[15px] [&>span]:!leading-5" aria-label={t("web.generated.e42cc67653")} translate="no">
                    <span className="v8os-wordmark__glow" aria-hidden="true">V8 Agent OS</span>
                    <span className="v8os-wordmark__shine" aria-hidden="true">V8 Agent OS</span>
                    <span className="v8os-wordmark__text">V8 Agent OS</span>
                </h1>
            </div>

            <div className="flex h-[25px] shrink-0 items-center gap-1.5">
                <Link href="/rpa">
                    <Button variant="ghost" size="sm" className="h-[25px] gap-1.5 rounded-lg px-2 text-[11px]">
                        <Workflow className="h-3.5 w-3.5" />
                        <span className="hidden leading-none sm:inline">{t("web.generated.6ee7a4c326")}</span>
                    </Button>
                </Link>
                <LocaleToggle />
                <VoiceToggle />
                <ThemeToggle />
                <UserProfile />
                {windowControls ? (
                    <div className="ml-1 flex h-[25px] items-center">
                        {windowControls}
                    </div>
                ) : null}
            </div>
        </header>
    );
}
