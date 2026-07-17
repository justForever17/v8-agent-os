"use client";

import { type ReactNode, useSyncExternalStore } from "react";
import {
    ProductShellTopbar,
    ProductSurfaceSwitcher,
    ProductTopbar,
    TopbarGlowActionButton,
} from "@v8/product-ui";
import { LocaleToggle } from "@/components/layout/LocaleToggle";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { UserProfile } from "@/components/layout/UserProfile";
import { VoiceToggle } from "@/components/layout/VoiceToggle";
import Link from "next/link";
import { useT } from "@/components/providers/LocaleProvider";
import { Bot } from "lucide-react";
import { ShellWindowControls } from "./ShellWindowControls";

const ADMIN_SURFACE_URL = "http://localhost:9528/admin";

const subscribeToShellSurface = () => () => {};
const readShellSurface = () => Boolean(window.v8osShell?.isShell);
const readServerShellSurface = () => false;

export function WebTopbar({ windowControls }: { windowControls?: ReactNode }) {
    const t = useT();
    const isShell = useSyncExternalStore(subscribeToShellSurface, readShellSurface, readServerShellSurface);

    const TopbarComponent = isShell ? ProductShellTopbar : ProductTopbar;
    const resolvedWindowControls = windowControls ?? (isShell ? <ShellWindowControls /> : undefined);
    const adminSurfaceItem = isShell
        ? {
            id: "admin",
            label: t("web.generated.surface.admin"),
            onSelect: () => window.v8osShell?.openAdmin(),
            title: t("web.generated.surface.openAdmin"),
        }
        : {
            id: "admin",
            label: t("web.generated.surface.admin"),
            href: ADMIN_SURFACE_URL,
            title: t("web.generated.surface.openAdmin"),
        };

    return (
        <TopbarComponent
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
                        adminSurfaceItem,
                    ]}
                />
            )}
            actions={(
                <>
                <TopbarGlowActionButton asChild tone="emerald" aria-label={t("web.rpa.title")} title={t("web.rpa.title")}>
                    <Link href="/rpa">
                        <Bot />
                        <span className="sr-only">{t("web.rpa.title")}</span>
                    </Link>
                </TopbarGlowActionButton>
                <LocaleToggle />
                <VoiceToggle />
                <ThemeToggle />
                <UserProfile />
                </>
            )}
            windowControls={resolvedWindowControls}
        />
    );
}

export const Topbar = WebTopbar;
