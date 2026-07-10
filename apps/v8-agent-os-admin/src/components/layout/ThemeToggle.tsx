"use client";

import * as React from "react";
import { Moon, Sun } from "lucide-react";
import { useProductTheme } from "@v8/product-ui";

import { useT } from "@/components/providers/LocaleProvider";
import { TopbarGlowActionButton } from "@/components/layout/TopbarGlowActionButton";

export function ThemeToggle() {
    const { resolvedTheme, setTheme, syncState } = useProductTheme();
    const [mounted, setMounted] = React.useState(false);
    const t = useT();

    React.useEffect(() => {
        setMounted(true);
    }, []);

    const label = syncState === "degraded" ? t("layout.theme.syncDegraded") : t("layout.theme.switcher");

    if (!mounted) {
        return (
            <TopbarGlowActionButton tone="amber" aria-label={label} title={label}>
                <span className="sr-only">{label}</span>
            </TopbarGlowActionButton>
        );
    }

    const isDark = resolvedTheme === "dark";

    return (
        <TopbarGlowActionButton tone={isDark ? "blue" : "amber"} onClick={() => void setTheme(isDark ? "light" : "dark")} aria-label={label} title={label}>
                <div className="relative flex h-full w-full items-center justify-center">
                    <Sun
                        className={isDark ? "absolute -rotate-90 scale-0 opacity-0 transition-all duration-500" : "absolute rotate-0 scale-100 opacity-100 transition-all duration-500"}
                    />
                    <Moon
                        className={isDark ? "absolute rotate-0 scale-100 opacity-100 transition-all duration-500" : "absolute rotate-90 scale-0 opacity-0 transition-all duration-500"}
                    />
                    {syncState === "degraded" ? <span className="absolute right-0 top-0 h-1.5 w-1.5 rounded-full bg-amber-500 ring-1 ring-background" /> : null}
                </div>
                <span className="sr-only">{label}</span>
        </TopbarGlowActionButton>
    );
}
