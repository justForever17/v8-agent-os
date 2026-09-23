"use client";

import { type ReactNode, useEffect, useSyncExternalStore } from "react";
import { usePathname, useRouter } from "next/navigation";
import {
    ProductShellTopbar,
    ProductSurfaceSwitcher,
    ProductTopbar,
} from "@v8/product-ui";
import { LocaleToggle } from "@/components/layout/LocaleToggle";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { UserProfile } from "@/components/layout/UserProfile";
import { BackgroundPlaybackControls, BackgroundVideoSoundToggle } from "@/components/layout/BackgroundVideoSoundToggle";
import { useT } from "@/components/providers/LocaleProvider";
import { ShellWindowControls } from "./ShellWindowControls";
import { RpaTopbarOverlay } from "./RpaTopbarOverlay";

const subscribeToShellSurface = () => () => {};
const readShellSurface = () => Boolean(window.v8osShell?.isShell);
const readServerShellSurface = () => false;

export function WebTopbar({ windowControls }: { windowControls?: ReactNode }) {
    const t = useT();
    const router = useRouter();
    const isShell = useSyncExternalStore(subscribeToShellSurface, readShellSurface, readServerShellSurface);

    const TopbarComponent = isShell ? ProductShellTopbar : ProductTopbar;
    useEffect(() => {
        if (!isShell) router.prefetch("/admin");
    }, [isShell, router]);
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
            onSelect: () => router.push("/admin"),
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
                <RpaTopbarOverlay />
                <LocaleToggle />
                <BackgroundVideoSoundToggle />
                <BackgroundPlaybackControls />
                <ThemeToggle />
                <UserProfile />
                </>
            )}
            windowControls={resolvedWindowControls}
        />
    );
}

export function Topbar() {
    const pathname = usePathname();
    if (pathname === "/login" || pathname === "/admin" || pathname?.startsWith("/admin/")) return null;
    return <WebTopbar />;
}
