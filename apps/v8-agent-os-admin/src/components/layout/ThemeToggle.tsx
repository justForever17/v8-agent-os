"use client";

import * as React from "react";
import { Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";

import { useT } from "@/components/providers/LocaleProvider";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

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
            <Button variant="ghost" size="icon" className="h-[25px] w-[25px] rounded-full" aria-label={label} title={label}>
                <span className="sr-only">{label}</span>
            </Button>
        );
    }

    const isDark = theme === "dark";

    return (
        <div className="group relative">
            <div
                className={cn(
                    "absolute inset-0 rounded-full opacity-0 blur-md transition-opacity duration-500 group-hover:opacity-100",
                    isDark
                        ? "animate-gradient-xy bg-gradient-to-r from-purple-600 via-blue-600 to-indigo-600"
                        : "animate-gradient-xy bg-gradient-to-r from-yellow-200 via-orange-200 to-pink-200",
                )}
            />

            <Button
                variant="ghost"
                size="icon"
                className={cn(
                    "relative h-[25px] w-[25px] overflow-hidden rounded-full border border-transparent transition-all duration-500 hover:border-border/50",
                    isDark
                        ? "bg-slate-950/50 text-blue-400 hover:bg-slate-900/80"
                        : "bg-white/50 text-orange-500 hover:bg-white/80",
                )}
                onClick={() => setTheme(isDark ? "light" : "dark")}
                aria-label={label}
                title={label}
            >
                <div className="relative flex h-full w-full items-center justify-center">
                    <Sun
                        className={cn(
                            "absolute h-3.5 w-3.5 rotate-0 scale-100 transition-all duration-500",
                            isDark ? "-rotate-90 scale-0 opacity-0" : "rotate-0 scale-100 opacity-100",
                        )}
                    />
                    <Moon
                        className={cn(
                            "absolute h-3.5 w-3.5 rotate-90 scale-0 transition-all duration-500",
                            isDark ? "rotate-0 scale-100 opacity-100" : "rotate-90 scale-0 opacity-0",
                        )}
                    />
                </div>
                <span className="sr-only">{label}</span>
            </Button>
        </div>
    );
}
