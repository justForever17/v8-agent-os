"use client";

import * as React from "react";
import { Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";

import { useT } from "@/components/providers/LocaleProvider";
import { TopbarGlowActionButton } from "@/components/layout/TopbarGlowActionButton";

export function ThemeToggle() {
    const { theme, setTheme } = useTheme();
    const [mounted, setMounted] = React.useState(false);
    const t = useT();

    React.useEffect(() => {
        setMounted(true);
    }, []);

    const label = t("layout.theme.switcher");

    if (!mounted) {
        return (
            <TopbarGlowActionButton tone="amber" aria-label={label} title={label}>
                <span className="sr-only">{label}</span>
            </TopbarGlowActionButton>
        );
    }

    const isDark = theme === "dark";

    return (
        <TopbarGlowActionButton tone={isDark ? "blue" : "amber"} onClick={() => setTheme(isDark ? "light" : "dark")} aria-label={label} title={label}>
                <div className="relative flex h-full w-full items-center justify-center">
                    <Sun
                        className={isDark ? "absolute -rotate-90 scale-0 opacity-0 transition-all duration-500" : "absolute rotate-0 scale-100 opacity-100 transition-all duration-500"}
                    />
                    <Moon
                        className={isDark ? "absolute rotate-0 scale-100 opacity-100 transition-all duration-500" : "absolute rotate-90 scale-0 opacity-0 transition-all duration-500"}
                    />
                </div>
                <span className="sr-only">{label}</span>
        </TopbarGlowActionButton>
    );
}
