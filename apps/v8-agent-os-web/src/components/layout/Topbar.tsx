"use client";

import type { ReactNode } from "react";
import { ProductSurfaceSwitcher, ProductTopbar, TopbarGlowActionButton } from "@v8/product-ui";
import { LocaleToggle } from "@/components/layout/LocaleToggle";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { UserProfile } from "@/components/layout/UserProfile";
import { VoiceToggle } from "@/components/layout/VoiceToggle";
import Link from "next/link";
import { useT } from "@/components/providers/LocaleProvider";
import { Bot } from "lucide-react";

const ADMIN_SURFACE_URL = "http://localhost:9528/admin";

export function WebTopbar({ windowControls }: { windowControls?: ReactNode }) {
    const t = useT();

    return (
        <ProductTopbar
            brandImageSrc="/product-mark.png"
            brandLabel={t("web.generated.e42cc67653")}
            surfaceSwitcher={(
                <ProductSurfaceSwitcher
                    ariaLabel={t("web.generated.surfaceSwitcher")}
                    items={[
                        {
                            id: "chat",
                            label: t("web.generated.surface.chat"),
                            active: true,
                            title: t("web.generated.surface.currentChat"),
                        },
                        {
                            id: "admin",
                            label: t("web.generated.surface.admin"),
                            href: ADMIN_SURFACE_URL,
                            title: t("web.generated.surface.openAdmin"),
                        },
                    ]}
                />
            )}
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
