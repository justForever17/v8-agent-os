"use client";

import type { ReactNode } from "react";
import { ProductTopbar, TopbarGlowActionButton } from "@v8/product-ui";
import { LocaleToggle } from "@/components/layout/LocaleToggle";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { UserProfile } from "@/components/layout/UserProfile";
import { VoiceToggle } from "@/components/layout/VoiceToggle";
import Link from "next/link";
import { useT } from "@/components/providers/LocaleProvider";
import { Bot } from "lucide-react";

export function WebTopbar({ windowControls }: { windowControls?: ReactNode }) {
    const t = useT();

    return (
        <ProductTopbar
            brandImageSrc="/product-mark.png"
            brandLabel={t("web.generated.e42cc67653")}
            actions={(
                <>
                <TopbarGlowActionButton asChild tone="emerald" aria-label={t("web.generated.6ee7a4c326")} title={t("web.generated.6ee7a4c326")}>
                    <Link href="/rpa">
                        <Bot />
                        <span className="sr-only">{t("web.generated.6ee7a4c326")}</span>
                    </Link>
                </TopbarGlowActionButton>
                <LocaleToggle />
                <VoiceToggle />
                <ThemeToggle />
                <UserProfile />
                </>
            )}
            windowControls={windowControls}
        />
    );
}

export const Topbar = WebTopbar;
