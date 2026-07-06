"use client";

import * as React from "react";
import { Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { TopbarGlowActionButton } from "@/components/layout/TopbarGlowActionButton";

export function ThemeToggle() {
    const { theme, setTheme } = useTheme();
    const [mounted, setMounted] = React.useState(false);

    React.useEffect(() => {
        setMounted(true);
    }, []);

    if (!mounted) {
        return (
            <TopbarGlowActionButton tone="amber">
                <span className="sr-only">Toggle theme</span>
            </TopbarGlowActionButton>
        );
    }

    const isDark = theme === "dark";

    return (
        <TopbarGlowActionButton tone={isDark ? "blue" : "amber"} onClick={() => setTheme(isDark ? "light" : "dark")}>
                <div className="relative flex h-full w-full items-center justify-center">
                    <Sun className={isDark ? "absolute -rotate-90 scale-0 opacity-0 transition-all duration-500" : "absolute rotate-0 scale-100 opacity-100 transition-all duration-500"} />
                    <Moon className={isDark ? "absolute rotate-0 scale-100 opacity-100 transition-all duration-500" : "absolute rotate-90 scale-0 opacity-0 transition-all duration-500"} />
                </div>
                <span className="sr-only">Toggle theme</span>
        </TopbarGlowActionButton>
    );
}
